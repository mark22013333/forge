# 踩坑表：自架 Azure DevOps Server 導入 CI

這 59 條都是實際導入時遇到的問題，不是從文件整理來的。每條都列症狀、根因、解法、驗證狀態。

第 1～24 條來自第一個導入的專案；第 25～33 條來自第二個專案（Gradle 8.14.3、主 repo＋依賴 repo，接在已經裝好的同一台 server 上）；第 34～44 條來自第三個專案（Gradle 7.5.1、主 repo＋依賴 repo，另有一個獨立 repo，接在同一台 server 上，並做了從 Windows agent 經 SSH 部署到 Linux VM 的 CD）；第 45～51 條來自第四次導入（在一台全新的 Windows Server 2022 上安裝 SQL Server 2025 Express 具名執行個體與 Azure DevOps Server 25H2，再把一個既有專案的 CI／CD 從舊 server 搬過去）；第 52～59 條來自第五次導入（把一個 Gradle 專案的 CI 搬到另一台以 **http** 提供的既有 Azure DevOps Server、在同一台主機新增 agent，並以 Windows agent 經 SSH 部署到 Linux VM 的 CD 跑通 dry-run）。

## 驗證狀態的意思

| 狀態 | 意思 |
|------|------|
| **已驗證** | 有對照實驗或重現證據支撐根因與解法 |
| **已遇到** | 實際發生過，解法當下有效；根因沒有另外做對照實驗證明 |
| **估計** | 數字是推估，沒有量測過 |
| **未釐清** | 現象確定存在，原因還不知道 |

引用這份表時，請保留狀態標註，不要把「估計」或「未釐清」寫成肯定句。

實測環境：ADO Server Express＋SQL Server 2025 Express（具名執行個體），裝在 Windows 11 專業版工作站。這個 OS 組合不在官方支援清單內（見第 21 條）。第四次導入的環境是 Windows Server 2022（工作群組、未加入網域，雲端 VM）＋SQL Server 2025 Express（具名執行個體）＋Azure DevOps Server 25H2。

依主題分類，所以編號不連續。

---

## 安裝與主機

### 1. SQL Server 2025 用 NetworkService 當服務帳戶，資料庫引擎起不來

- **症狀**：ADO 安裝精靈的「安裝 SQL Express」失敗，結束碼 -2061893606。SQL ERRORLOG 依序出現 17190（FallBack certificate failed，error 15 state 29）→ 17182（0x80092004）→ 17826。
- **根因**：服務帳戶。改用虛擬帳戶 `NT Service\MSSQL$<執行個體名稱>` 手動安裝即正常。已排除磁碟扇區大小、TLS 1.2 設定這兩個方向。
- **解法**：先手動安裝 SQL Server，服務帳戶選虛擬帳戶；再到 ADO 精靈選「使用現有執行個體」。
- **驗證狀態**：已驗證

### 2. RebootRequiredCheck 永遠失敗

- **症狀**：SQL 安裝與解除安裝都卡在「需要重新開機」，重開機後仍然一樣。
- **根因**：登錄機碼 `PendingFileRenameOperations` 在重開機後仍不會清空。裡面累積了瀏覽器、Office、印表機 spool 的殘留檔。
- **解法**：安裝與解除安裝都加 `/SKIPRULES=RebootRequiredCheck`。ADO 精靈自己呼叫 SQL 安裝時也帶這個參數。
- **驗證狀態**：已遇到

### 3. ping 得到主機，但區網連不上 ADO 的連接埠

- **症狀**：區網其他機器 ping 得通，瀏覽器開 ADO 網址逾時。
- **根因**：網卡的網路類型是「公用（Public）」，精靈建立的防火牆規則只套用在網域與私人設定檔。
- **解法**：把網路類型改成「私人（Private）」，或另外建一條限定來源網段（例：`192.0.2.0/24`）的防火牆規則。
- **驗證狀態**：已遇到

### 21. Windows 11 舊版本不在支援清單

- **症狀**：安裝前的檢查可以通過，但 OS 版本不在官方列出的 client OS 支援清單（實測當時清單只列 24H2）。
- **根因**：屬於不受支援的組合，出問題時沒有官方支援可以依靠。另外，SQL Server Developer 版授權只限開發測試，不能當日常服務用。
- **解法**：正式使用前，先對照官方支援清單確認 OS 版本；資料庫選 Express 或正式授權版本。
- **驗證狀態**：已遇到（支援清單與授權條款是事實；「不受支援但能裝」只在單一環境觀察過）

### 19. 網頁的「新增代理程式」對話框當掉

- **症狀**：Agent pools 頁面的「新增代理程式」對話框沒有內容，套件清單是空的（前端 JS 錯誤）。
- **根因**：網頁對話框的前端問題。
- **解法**：直接呼叫 REST API `_apis/distributedtask/packages/agent?platform=win-x64`，取得伺服器指定的 agent 版本與下載網址。
- **驗證狀態**：已遇到

### 47. 安裝精靈選錯部署類型，要你從空的清單選既有資料庫

- **症狀**：全新安裝、要用已裝好的 SQL 具名執行個體，精靈卻要求從資料庫清單選一個既有的 ADO 資料庫，而清單是空的。另外，SQL 執行個體欄位只寫執行個體名稱時，被當成電腦名稱去找。
- **根因**：選到「現有的部署」——那是給「資料庫已存在（升級或搬移）」用的。另外，SQL 執行個體欄位只寫 `<執行個體>` 時，會被當成電腦名稱解析。
- **解法**：全新安裝並指定既有 SQL 具名執行個體時，選「**新的部署 - 進階**」。執行個體欄位寫 `.\<執行個體>` 或 `localhost\<執行個體>`。
- **驗證狀態**：已遇到

### 49. 公用 URL 預設是主機的電腦名稱，用戶端連不到

- **症狀**：網頁、git clone 網址、通知信裡的連結都是 `http://<電腦名稱>:<port>`，用戶端開不起來，但改用 IP 直接連就正常。
- **根因**：公用 URL 預設取主機的電腦名稱。主機在雲端或工作群組（沒有網域 DNS）時，用戶端常解析不到這個名稱。
- **解法**：安裝時，或之後在管理主控台的「變更公用 URL」，改成用戶端連得到的位址（IP 或 FQDN，例：`http://192.0.2.10:8080`）。servers.yml 的 `url` 也用同一個位址。
- **驗證狀態**：已遇到

### 50. 套用 Patch：管理主控台開著就停止；修補後 Git SSH 服務可能沒有自動啟動

- **症狀**：執行 Patch 安裝程式時直接停止，提示要先關閉管理主控台。關閉後重新執行可完成修補，但輸出中有一行 `Skipping start of service since start mode is not set to Automatic: TeamFoundationSshService`。
- **根因**：修補工具要求管理主控台（`TfsMgmt.exe`）不在執行中。Git SSH 服務 `TeamFoundationSshService` 的啟動類型可能是「手動」，修補工具不會啟動它，〔推論，未實測〕重開機後這個服務不會自動啟動，Git over SSH 就會斷。
- **解法**：套用 Patch 前先關閉管理主控台。修補後檢查 SSH 服務的啟動類型，改成自動：`Set-Service TeamFoundationSshService -StartupType Automatic`，再 `Start-Service TeamFoundationSshService`。修補後的版本可在登錄 `HKLM\SOFTWARE\Microsoft\TeamFoundationServer\<版本>` 的 `PatchVersion` 確認。
- **驗證狀態**：已遇到

## 驗證與權限

### 4. ADO 內建 SSH 只接受 RSA 金鑰

- **症狀**：用 ed25519 金鑰推送失敗，訊息是 `Public key authentication failed`。
- **根因**：握手時伺服器的 `server-sig-algs` 只列出 `rsa-sha2-256`、`rsa-sha2-512`。
- **解法**：產生 CI 專用的 RSA 4096 金鑰。測試連線時加 `-o IdentitiesOnly=yes -o IdentityAgent=none`，避免 ssh-agent 裡的其他金鑰先通過、蓋掉真正的錯誤。
- **驗證狀態**：已遇到（握手內容有觀察紀錄）

### 5. IIS 開著基本驗證，所有 PAT 都回 401

- **症狀**：用個人存取權杖（PAT）呼叫 REST API 或 git over HTTPS，全部回 401。
- **根因**：IIS 的基本驗證會先把 PAT 當成 Windows 密碼去驗，驗不過就直接拒絕。
- **解法**：停用 IIS 基本驗證。**注意**：這和「Cloudflare Tunnel 需要基本驗證」互相衝突，對外存取方案要另外設計（見 SKILL.md 第 8 章）。
- **驗證狀態**：已驗證

### 23. PAT 的實際權限範圍可能比以為的大

- **症狀**：建了「唯讀」用途的權杖，實際上卻能修改 repo 設定。
- **根因**：範圍設定與實際授權的對應關係還沒完整查清楚。
- **解法**：發出前先檢查權杖的範圍設定。〔推測，未實測〕可以先拿它在測試 repo 上打一次寫入型 API，確認會被拒絕。反方向（範圍不足導致 401）見第 32 條。
- **驗證狀態**：未釐清（現象確定，範圍細節未查清）

### 32. 權杖範圍不足，寫入型 API 回 401

- **症狀**：同一個權杖查詢專案、建置都正常，但建專案、推送、排入建置時回 401。
- **根因**：權杖只有讀取範圍。各動作需要的範圍見 SKILL.md「權杖（PAT）與權限範圍」：建專案要 Project and Team（讀寫管理）、建 repo 與推送要 Code（讀寫）、建 pipeline 定義與排入建置要 Build（讀取與執行）。
- **第三個專案的實例**：同一個權杖能建專案、建 repo、改預設分支，但排入建置回 401，補上 **Build：讀取與執行**後正常；查詢 secure file 清單回 200、上傳卻回 401，補上 **Secure Files：讀取、建立及管理**後正常。「查得到、寫不進」就是範圍問題的典型樣子。
- **解法**：換用有對應範圍的權杖（servers.yml 的 `pat_env` 指向它），或改用網頁操作。401 還有另一個常見原因是 IIS 基本驗證（第 5 條）：所有權杖、連讀取都 401 時先查第 5 條；只有寫入 401 時查範圍。
- **第五次導入的實測**：在另一台 server 上依序遇到並補齊——建專案需「專案與小組：讀取、寫入及管理」；推 repo 與改預設分支需「程式碼：讀取及寫入」；建 pipeline 與排入建置需「建置：讀取及執行」；上傳 secure file 需「安全檔案：讀取、建立及管理」（只有讀取時清單查得到、上傳回 401）。
- **驗證狀態**：已遇到（唯讀權杖讀取正常、寫入 401；上列四個範圍在第五次導入逐一補上後該動作即成功；授權 `pipelinePermissions` 需要哪個範圍仍未驗證）

### 34. 用 REST 建 pipeline 回 401，但 pipeline 其實已經建好

- **症狀**：`POST _apis/pipelines` 回 401（或回應不是 JSON），看起來像建立失敗。
- **根因**：權杖缺 Build：讀取與執行時，定義本身已建立，401 來自建立之後的後續動作（推測，未查證是哪一步）。
- **解法**：**不要直接重送**，先 `GET _apis/pipelines` 列出確認；已存在就讀回它的 `configuration` 檢查 yaml 路徑與 repo id。〔推測〕重送可能建出名稱重複的第二條（未實測）。
- **驗證狀態**：已遇到（兩條 pipeline 都出現同樣情況，列表確認皆已建成；根因未做對照實驗）

### 35. 新專案的 pipeline 一直停在 notStarted，agent 卻閒著

- **症狀**：手動觸發成功（HTTP 200、拿到 run id），但建置永遠 `notStarted`；agent 在線且閒置，agent pool 的排隊清單裡也看不到這個工作。
- **根因**：新專案的 pipeline 還沒被授權使用資源：agent 佇列（`queue`）、yaml 裡 `resources.repositories` 引用的其他 repo（`repository`，id 為 `<專案 id>.<repo id>`）、`DownloadSecureFile` 用到的 secure file（`securefile`）。網頁上會出現「需要權限」的 Permit 按鈕，REST 觸發時看不到。
- **解法**：`GET _apis/pipelines/pipelinePermissions/<type>/<id>?api-version=6.1-preview.1` 看授權清單（空的就是原因），`PATCH` 同一路徑、body `{"pipelines":[{"id":<pipeline id>,"authorized":true}]}`，再讀回確認。可用 `scripts/ado-authorize.sh`。
- **驗證狀態**：已驗證（授權清單為空 → PATCH 後同一次建置隨即開始執行）

### 48. 工作群組主機：ADO 使用者就是主機上的本機 Windows 帳號

- **症狀**：主機沒有加入網域，不知道怎麼新增 ADO 使用者；人員選取器搜尋帳號名稱找不到人；照文件找英文群組名稱（例：Project Collection Administrators）找不到。
- **根因**：工作群組主機上，ADO 的使用者就是這台主機的本機 Windows 帳號。介面選了非英文語言時，內建群組名稱也會被翻譯（例：繁中介面的「專案集合系統管理員」）。
- **解法**：新增使用者要先在主機上建立本機 Windows 帳號，再到 ADO 把它加進群組。登入時帳號寫 `<主機名>\<帳號>`；人員選取器搜尋不到時，輸入完整的 `<主機名>\<帳號>`。依文件找群組時，用介面語言的翻譯名稱找。
- **驗證狀態**：已遇到

### 52. Server 以 http 提供時，agent 設定用 PAT 失敗

- **症狀**：新增 agent 時 `config.cmd --auth pat` 失敗，訊息是 `Basic authentication requires a secure connection to the server.`。
- **根因**：PAT 走基本驗證，agent 拒絕在非 https 的連線上送出基本驗證（依錯誤訊息判斷，未另做對照實驗）。
- **解法**：改用 `--auth integrated`：以執行 `config.cmd` 的 Windows 帳號驗證，不需要任何權杖。serverUrl 照抄同一台既有 agent 目錄下 `.agent` 檔的 `serverUrl`（實例是伺服器根網址，形如 `http://ado-host:8080/`，**不含** collection）。完整步驟（只用 ASCII，可直接貼到 Windows 主控台；版本換成既有 agent 的版本）：
  ```powershell
  $ver = '4.260.0'
  Invoke-WebRequest "https://download.agent.dev.azure.com/agent/$ver/vsts-agent-win-x64-$ver.zip" -OutFile "D:\vsts-agent-$ver.zip"
  Expand-Archive "D:\vsts-agent-$ver.zip" -DestinationPath 'D:\agent2'
  cd D:\agent2
  .\config.cmd --unattended --url http://ado-host:8080/ --auth integrated --pool Default --agent agent-02 --work _work --runAsService --windowsLogonAccount "NT AUTHORITY\NETWORK SERVICE"
  ```
  加 agent 前先依第 22 條量容量；裝好後第一次建置連線跑（第 38 條）。不要為了設定 agent 請使用者產生權杖；使用者若已在對話或文件中貼出權杖原文，立即提醒撤銷。
- **驗證狀態**：已遇到（PAT 失敗、integrated 成功；agent 與 ADO 在同一台；4.260.0 的下載網址實際可下載。agent 裝在**另一台**時 integrated 是否可用**未驗證**，〔推測〕需要兩台在同一網域或有相同的本機帳號）

## Agent 與建置環境

### 6. agent 服務的 PATH 裡沒有 git，checkout 卻正常

- **症狀**：agent 服務的 PATH 裡沒有 git，但 checkout 步驟正常。〔推論〕因此腳本步驟直接呼叫 `git` 應會找不到指令。
- **根因**：agent 內附一份 git，位置在 `<agent 目錄>\externals\git\cmd\git.exe`，checkout 用的是這份。
- **解法**：腳本步驟需要 git 時，直接用內附的這份完整路徑。
- **驗證狀態**：已驗證

### 7. NetworkService 沒有使用者資料夾，npm 快取寫入失敗

- **症狀**：npm 安裝出現 `EPERM -4048`。
- **根因**：agent 以 NetworkService 身分執行，沒有自己的使用者資料夾，npm 快取預設落在 `C:\Users\Default`，沒有寫入權限。
- **解法**：把 `NPM_CONFIG_CACHE` 指到一個 agent 有寫入權限的共用路徑。
- **驗證狀態**：已遇到

### 8. Gradle 預設 heap 512MB，編譯時 GC 過載中止

- **症狀**：編譯到一半中止，訊息是 GC overhead 類的錯誤。
- **根因**：專案沒有 `gradle.properties`，開發者各自靠 `~/.gradle` 裡的個人設定；CI 上沒有這份設定，就用預設 512MB。
- **解法**：在 pipeline 明確傳 `-Dorg.gradle.jvmargs=-Xmx3g -XX:MaxMetaspaceSize=1g`，CI 不要依賴任何人的個人設定。
- **驗證狀態**：已遇到

### 22. 多個 agent 並行

- **症狀**：一個 agent 一次只能跑一個 job，排隊時間長。
- **根因**：Server 版自架 agent 不收並行費用，並行數等於 agent 數，所以要增加並行就要多裝 agent。
- **解法**：同一台機器多裝幾個 agent，各自使用不同的資料夾與名稱。會改資料的 E2E（例如 Playwright）不可並行，要放獨立的 pool，或用 `lockBehavior: sequential`。
- **加 agent 前先量容量**（第五次導入）：在建置**進行中**量主機記憶體與前十名程序，不要用閒置時的數字：
  ```powershell
  Get-CimInstance Win32_OperatingSystem | Select-Object @{n='TotalGB';e={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}},@{n='FreeGB';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}}
  Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name,Id,@{n='WS_GB';e={[math]::Round($_.WorkingSet64/1GB,2)}}
  ```
  （給使用者在 Windows 主控台貼上的指令只用 ASCII，見第 53 條。）實例：64GB 主機、兩個建置同時跑時剩 17.5GB；一個 Gradle 建置（daemon `-Xmx3g`＋測試 fork 3G）約佔 9～10GB；同一台還常駐 ADO 的 `w3wp`／`TfsJobAgent`、SQL Server、Oracle。據此只多加 1 個 agent。
- **驗證狀態**：「每個建置 6～8GB」仍是**估計**；第五次導入**量測**到單一 Gradle 建置（`-Xmx3g`＋測試 fork 3G）約 9～10GB，是單一專案的實測值，不是通則。兩個 agent 並行有實際重疊 5 分鐘且都成功，但這不是完整的壓力測試。

### 40. agent 內附的 git 是 MinGit，沒有 bash 與常用工具

- **症狀**：想在 pipeline 裡直接跑既有的 bash 部署腳本，但 agent 上找不到 bash。
- **根因**：agent 的 `externals\git` 是精簡版 MinGit：有 `ssh.exe`、`awk`、`sed`、`mktemp`，**沒有** `bash.exe`、`scp`、`unzip`、`sha256sum`、`stat`、`dd`。
- **解法**：Windows agent 上的部署邏輯改用 PowerShell 5.1，連線用 Windows 內建的 `C:\Windows\System32\OpenSSH\ssh.exe`／`scp.exe`；需要在目標機執行的部分仍可用 bash（把腳本 scp 過去再執行）。見 `cd-ssh-vm.md`。
- **驗證狀態**：已驗證（以探測 pipeline 逐一檢查工具是否存在）

### 41. Windows PowerShell 5.1 讀無 BOM 的 .ps1，中文變亂碼、整支腳本無法解析

- **症狀**：repo 裡的 `.ps1` 在 agent 上執行時報一連串 `Unexpected token '}'`、`Missing closing ')'`，錯誤訊息裡的中文是亂碼。
- **根因**：Windows PowerShell 5.1 讀取沒有 BOM 的檔案時，用系統 ANSI 字碼頁（繁中 Windows 為 Big5）解讀，UTF-8 的中文位元組被拆錯，吃掉了後面的引號。
- **解法**：repo 內給 Windows PowerShell 5.1 執行的 `.ps1` 一律存成 **UTF-8 with BOM**，並在檔頭註明。推送前可用 PowerShell 7（官方 tarball 免安裝）以「有 BOM 讀 UTF-8、否則讀 Big5」模擬 5.1 解析，省下一輪建置。yaml 內嵌的 `powershell:` 腳本不受影響。
- **驗證狀態**：部分驗證（根因：本機以 Big5 模擬讀取重現 16 個解析錯誤，加 BOM 後 0 個；修法：加 BOM 後尚未在 agent 上執行過）

### 42. PowerShell 在 ErrorActionPreference=Stop 時，原生程式寫 stderr 就中止

- **症狀**：腳本呼叫 `ssh.exe` 等原生程式，只是印出一行警告（例如 `Warning: Permanently added ... to the list of known hosts`），整個步驟就以 `NativeCommandError` 失敗。
- **根因**：Windows PowerShell 5.1 在 `2>&1` 重導或 `$ErrorActionPreference = 'Stop'` 時，把原生程式的 stderr 輸出當成錯誤紀錄處理（依 PowerShell 的已知行為判斷，未另做對照實驗）。
- **解法**：在包裝原生程式的函式內暫時設 `$ErrorActionPreference = 'Continue'`，成敗改看 `$LASTEXITCODE`；ssh 另加 `-o LogLevel=ERROR` 減少雜訊。
- **驗證狀態**：已遇到（探測 pipeline 的連線本身成功，步驟卻因這行警告失敗）

### 44. Windows 版 OpenSSH 8.1 設了 ConnectTimeout，每次連線都等滿逾時

- **症狀**：agent 上每一次 `ssh`／`scp` 都固定多花好幾秒，而且秒數非常一致；分段上傳時每段固定多出約 2×ConnectTimeout，整體上傳慢十倍以上，最後撞到 job 逾時被取消。
- **根因**：Windows 內建的 `OpenSSH_for_Windows_8.1p1` 在設了 `ConnectTimeout` 時，連線會等滿這個秒數才回報成功（`ssh -v` 可看到 `Connecting to ...` 與 `Connection established` 之間剛好隔 ConnectTimeout 秒）。網路本身沒問題：同一台 agent 用 .NET 直接建 TCP 連線只要 0.05 秒。
- **解法**：agent 端的 ssh／scp **不要設 `ConnectTimeout`**；需要逾時保護時在外層做（例如分段上傳每段以 `Start-Process` 啟動、逾時就 `taskkill /T`）。排查時先用探測 pipeline 量「ssh true」的耗時，與 .NET TcpClient 的連線時間對照。
- **驗證狀態**：已驗證（同一台 agent 上 `ssh true`：ConnectTimeout=5／30／不設 → 5.6／30.6／0.7 秒，各兩次一致；不設時 scp 3MB 約 1.5 秒）

### 51. agent 與 ADO 同一台：驗證不用 PAT、JDK 能力自動偵測、共用快取的權限

- **症狀**：照一般做法要先產生 PAT 才能設定 agent；agent 上線後能力清單已經有 `jdk_17_x64` 等項目，即使系統 `JAVA_HOME` 指向別的版本。
- **根因**：agent 與 ADO 裝在同一台時，`config.cmd` 的驗證類型預設是 Integrated（以目前登入的 Windows 帳號驗證），不需要 PAT；agent 裝在別台才需要 PAT。agent 會自行偵測已安裝的 JDK 並登記成能力，不看 `JAVA_HOME`。
- **解法**：同機安裝時直接用預設的 Integrated。pipeline 的 `demands` 可直接用自動登記的 `jdk_17_x64` 這類能力。agent 以 NETWORK SERVICE 執行時，共用快取目錄（Gradle、npm）要給它修改權限：`icacls <目錄> /grant "NT AUTHORITY\NETWORK SERVICE:(OI)(CI)M"`，否則別的帳號放進去的檔案它改不動（同類問題見第 7 條）。
- **驗證狀態**：Integrated 驗證與 JDK 能力自動偵測為已遇到；快取目錄權限為**未驗證（預防）**，沒有實際發生過寫入失敗

### 54. 重啟 agent 服務會取消正在跑的建置

- **症狀**：為了讓 agent 讀到新的 PATH（例如剛裝了 python，第 57 條）而 `Restart-Service vstsagent*`，正在執行的建置被取消，ADO 記錄「代理程式已收到關機訊號」。
- **根因**：重啟服務會讓 agent 中止手上的 job。
- **解法**：請使用者重啟前先確認 agent 閒置：Agent pools → 該 agent 的 Jobs 沒有執行中的項目。判斷執行中的是哪個專案的建置見第 55 條。
- **驗證狀態**：已遇到

### 55. agent 集區被多個專案共用，建置編號與 pipeline 名稱可能完全相同

- **症狀**：想確認 agent 正在跑哪個建置，看到的建置編號（例：`20261013.3`）與 pipeline 名稱（例：`<repo> CI`）都和自己專案的一樣，差點誤判成「自己已取消的建置卡住 agent」——其實是同一個 collection 裡別的專案的建置。
- **根因**：`Default` 等 agent 集區屬於 collection，同一個 collection 的專案都可能使用；建置編號預設是日期＋序號，pipeline 名稱預設是 `<repo> CI`，不同專案很容易撞名。
- **解法**：看該 agent 的 jobrequest 的 `scopeId`（＝專案 id）判斷是哪個專案，不要只看名稱或編號。專案 id 用 `GET {collection}/_apis/projects/<專案>` 取得。
- **驗證狀態**：已遇到

### 57. agent 的能力清單沒有 python，不代表 agent 找不到 python

- **症狀**：agent 的 systemCapabilities 沒有 `python`（也不一定列出 PATH），容易據此判定 agent 上沒有 python；或主機上明明裝過 python，pipeline 卻找不到。
- **根因**：能力清單不保證回報 PATH 或 python。另外，裝在使用者個人帳號下的 Python（`AppData\Local\Programs\Python`）只在那個帳號的 PATH 裡，agent 以 NETWORK SERVICE 執行時讀不到。
- **解法**：用探測 pipeline 實證（`Get-Command python`、`python --version`），不要看能力清單下結論。要給 agent 用就裝成全機共用：`python-3.12.x-amd64.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0`（裝在 `C:\Program Files\Python312`），用 `[Environment]::GetEnvironmentVariable("Path","Machine")` 確認系統層 PATH 已含該路徑，再重啟 agent 服務（先確認閒置，第 54 條）。pipeline 裡叫 `python`；這種安裝**沒有** `python3`、`py`。
- **驗證狀態**：已遇到（全機安裝並重啟 agent 後，探測 pipeline 以 `python` 找得到）

## 測試執行

### 9. 測試需要環境變數與設定檔

- **症狀**：部分測試在 static 初始化階段就 NPE；另一批測試因為找不到 Hibernate 設定檔而失敗。
- **根因**：缺少 `*_HOME` 這類環境變數。找不到 Hibernate 設定檔的那批，其實是需要資料庫的整合測試。
- **解法**：在 pipeline 先設好需要的環境變數；整合測試用「只跑近期修改」（第 12 條）篩選，或直接排除。
- **驗證狀態**：已遇到

### 10. build.gradle 裡的 `failFast = true` 讓一個失敗後全部停跑

- **症狀**：只跑了 3 個測試就停了。
- **根因**：`build.gradle` 的 `test {}` 設了 `failFast = true`。
- **解法**：用 init script 關掉，而且**必須寫在 `gradle.projectsEvaluated` 裡面**，否則會被 `build.gradle` 的 `test {}` 蓋回去。見 `ci-init.gradle`。
- **驗證狀態**：已驗證（最小專案對照：寫在外層時仍是 true，寫在 `projectsEvaluated` 內才是 false）

### 11. 測試共用靜態狀態

- **症狀**：在同一個 JVM 裡跑時，整個測試類別出現 `NotAMockException`；同一個類別單獨跑卻會通過。
- **根因**：多個測試類別共用同一份靜態狀態（例如 mock 用的 Spring context）。
- **解法**：`forkEvery = 1`，讓每個測試類別用獨立 JVM。**反面情況**：有測試要靠「別的測試先初始化好某個元件（例如快取叢集）」才會通過，獨立 JVM 下會 NPE（本機單獨跑也重現）。這類測試要排除，寫明原因並列為待修。
- **驗證狀態**：已遇到（反面情況本機重現過）

### 12. 只跑近期修改過的測試

- **症狀**：整套測試太久，或有大量整合測試在 CI 上跑不起來。
- **根因**：無（這是策略，不是故障）。
- **解法**：checkout 設 `fetchDepth: 0`。用 PowerShell 呼叫 agent 內附的 git（第 6 條），列出 `--since="N days ago"` 內修改過的測試類別，寫成清單檔，再交給 init script 做 `includeTestsMatching`。有依賴 repo 時，**掃描要涵蓋依賴 repo 的 checkout 目錄**，否則依賴 repo 裡新改的測試永遠不會被選中。
- **清單為空時的兩種策略**（範本參數 `emptyRecentTests`）：
  - `fail`（預設）：讓建置失敗，避免「零測試、建置通過」的假綠燈。適合測試持續有人維護的專案。
  - `buildOnly`：**明確切換成只建置**（例：`assemble testClasses`），log 寫明「本次不跑測試」，不載入 init script、不發佈測試結果。適合測試多年沒動的專案：第三個專案的測試最後修改都在 180 天以前，用 `fail` 會每次建置都紅。init script 仍然拒絕空清單，兩種策略都不會產生假的「測試通過」。
- **驗證狀態**：已遇到（`buildOnly` 在第三個專案以同結構的專案檔跑綠，連線與離線各一次）

### 13. 依賴版本被釘在舊版的測試引擎衝突

- **症狀**：`NoClassDefFoundError: CleanupMode`。
- **根因**：`junit-jupiter-api` 被固定在 5.8.2，但依賴鏈帶進了較新的引擎。**這個子專案本來就是壞的**，本機跑也一樣失敗。
- **解法**：CI 暫時用 `-x <子專案>:test` 排除，並列為待辦。〔推測〕根本修法應是統一 JUnit 相關套件的版本，尚未實作驗證。
- **驗證狀態**：已遇到（本機同樣失敗）

### 20. 發佈測試結果時遇到 SQL Express 死結

- **症狀**：多組測試結果同時寫入時發生死結。測試分頁顯示的數字和 Gradle 輸出的數字不同。
- **根因**：死結本身與 SQL Express 同時寫入有關；**兩邊數字為什麼不同，還沒查清楚**。
- **解法**：建置成敗以 Gradle 的結果為準；測試分頁的數字可能不完整，不要拿它當唯一依據。
- **驗證狀態**：**未釐清**（數字差異的原因）

## 原始碼與建置

### 45. secure file 不存在或未授權，整條 pipeline 在驗證階段就失敗

- **症狀**：建置幾秒內就 completed/failed；timeline API 回 **HTTP 204**（沒有任何 record）；即使這次的 condition 根本不會執行用到 secure file 的那個 stage，也一樣失敗。
- **根因**：YAML 引用的 secure file 在 pipeline 派工前的驗證階段就會檢查。找不到或未授權時整條 pipeline 無效，錯誤只記在 `GET _apis/build/builds/<id>` 回應的 `validationResults` 裡，訊息類似「管線無效。作業 deploy: 找不到步驟 X 輸入 secureFile 參考的安全檔案 deploy-key」。
- **解法**：建置秒失敗且 timeline 為空時，先看 build 的 `validationResults`（`scripts/ado-run.sh` 會自動印出）。搬到新 server 時，secure file 要在第一次建置前上傳並授權 pipeline（授權見第 35 條、`scripts/ado-authorize.sh`）。
- **驗證狀態**：已驗證

### 46. 「匯入存放庫」的入口不同，結果不同：會另外新建 repo

- **症狀**：先建好同名的空 repo，再從 Repos 頁面上方的 repo 下拉選單選「匯入存放庫」，結果多出一個名稱帶 `.git` 的新 repo（例：`product-core.git`），原本的空 repo 還是空的。
- **根因**：下拉選單的「匯入存放庫」一律**新建** repo，名稱預設取來源網址最後一段（含 `.git`），即使同名空 repo 已存在。要匯入既有的空 repo，得在該空 repo 的頁面上用「匯入」。
- **解法**：要匯入既有空 repo 時，先切到那個 repo 再按它頁面上的「匯入」。匯入會帶入來源的所有分支，預設分支沿用來源，匯入後要改成正式分支（PATCH 要用 repo id，見第 18、27 條）。優點：由 ADO server 直接從 Git 平台拉，不經過開發者電腦或 VPN，適合大型 repo（實例約 940MB）。
- **驗證狀態**：已遇到

### 14. 多 repo 並排 checkout

- **症狀**：建置時 `cannot find symbol`。
- **根因**：`settings.gradle` 用相對路徑引用同一層的另一個 repo，pipeline 要把兩個 repo 並排 checkout。被引用 repo 的分支如果落後（有人只推到別的分支，或根本沒推），就會編譯失敗。
- **解法**：建置前先確認被引用 repo 的對應分支含有需要的 commit。
- **驗證狀態**：已遇到

### 15. 離線建置

- **症狀**：建置過程重複下載依賴，或是外部套件庫不穩定時建置失敗。
- **根因**：無（這是策略）。注意兩點：動態版本（例如 `junit:junit:4+`）的快取只保留 24 小時，離線時過期就解析不到；Azure Pipelines 範本運算式**沒有 `iif`**。
- **解法**：用參數控制 `--offline`（預設開啟）。條件值改用 `variables` 清單形式，搭配 `${{ if }}` 寫。
- **驗證狀態**：已遇到（實測離線建置 0 筆下載且通過）

### 25. 離線建置在新的 Gradle 版本上第一個外掛就找不到

- **症狀**：同一個 agent、同一個 `GRADLE_USER_HOME`，舊專案（Gradle 7.5.1）離線建置正常；新專案（Gradle 8.14.3）離線建置在解析第一個外掛時就報「not found」。
- **根因**：Gradle 的依賴解析快取依 Gradle 版本分開存放。快取裡有這些套件，但只有舊版本解析過；新版本離線時讀不到。
- **解法**：每個新的 Gradle 版本第一次建置要連線：手動執行 pipeline，把 offline 參數取消勾選跑一次。成功後把版本補進 servers.yml 的 `cached_gradle_versions`，之後就能離線。`ci-discover.sh` 會比對專案的 `gradle-wrapper.properties` 與這份清單並提醒。
- **驗證狀態**：已遇到（連線跑一次後離線建置恢復正常；「依版本分開」是依快取目錄結構的判讀，未另做對照實驗）

### 26. ADO 預設的 job 逾時 60 分鐘，首次建置被取消

- **症狀**：第一次建置跑到一半被取消，結果是 canceled 而不是 failed。
- **根因**：沒寫 `timeoutInMinutes` 時 job 預設 60 分鐘。實例中第一次連線建置光下載依賴約 20 分鐘，`forkEvery = 1` 的測試約 40～50 分鐘，合計超過 60。
- **解法**：範本改用 `jobs:` 結構並設 `timeoutInMinutes`（參數化，預設 120）。只寫 `steps:` 的 pipeline 沒有地方放 job 層級的逾時。
- **驗證狀態**：已遇到（改成 120 分鐘後同一份 pipeline 跑完；時間是單一專案的實例，不是通則）

### 30. 多模組專案用 `test` 會連帶編譯本來就壞的子專案

- **症狀**：`gradlew test` 在某個子專案的 `compileJava` 就失敗，主專案的測試根本沒跑到。該子專案本機也編不過。
- **根因**：不帶專案路徑的 `test` 會在所有子專案上執行，包含本來就壞的那個。
- **解法**：明確指定要跑的專案，例如 `:test :product-core:test`（`:test` 是根專案，`:product-core:test` 是依賴 repo 的專案）。範本的 `gradleTasks` 參數就是為這個留的；.ci-tools.yml 用 `gradle_tasks` 記錄。和第 13 條的 `-x <子專案>:test` 的差別：`-x` 只跳過測試，子專案仍會編譯。
- **驗證狀態**：已遇到

### 33. 首次完整建置冒出大量產品既有的失敗

- **症狀**：第一次跑全部測試，失敗數十個方法、橫跨十幾個類別，看起來像本專案改壞了很多東西。
- **根因**：大多是產品原始版本本來就有的問題，或 CI 環境與開發機不同（Windows 路徑分隔符、被 gitignore 的設定檔不在 CI、需要容器或外部服務、外部網域已不存在、測試沒跟上主程式的修改）。實例中 18 個失敗類別的測試檔與產品基準分支相比全部沒有變更。
- **解法**：照 SKILL.md「5.1 首次建置失敗分類」的流程：`ci-triage.sh` 從 log 抽出失敗類別 → 本機用相同 init script 只跑這些類別 → 與產品基準分支比對測試檔 → 本機也失敗＝既有問題、本機通過＝環境差異 → 都寫進 `ci-init.gradle` 排除清單，每筆附原因與移除條件，另開單追蹤。不要為了變綠去改測試。
- **驗證狀態**：已遇到（`ci-triage.sh` 以該次建置的 log 重跑，抽出的 18 個類別與人工整理的排除清單一致）

### 18. ADO repo 的預設分支是第一個被推上去的分支

- **症狀**：匯入後 repo 的預設分支變成測試分支。
- **根因**：ADO 把第一個收到的分支設為預設分支。
- **解法**：匯入後立刻用 REST API `PATCH _apis/git/repositories/{id}` 把 `defaultBranch` 改成正式分支。路徑要用 repo 的 **id**（第 27 條）。
- **驗證狀態**：已遇到

### 27. PATCH 預設分支時用 repo 名稱回 400

- **症狀**：`PATCH _apis/git/repositories/<repo 名稱>` 改 `defaultBranch` 回 400；同一個 API 的 GET 用名稱卻正常。
- **根因**：這個 PATCH 只接受 repo 的 id（GUID），不接受名稱。
- **解法**：先 `GET _apis/git/repositories/<名稱>` 取出 `id`，再用 id 呼叫 PATCH。
- **驗證狀態**：已遇到

### 28. pipeline 檔只在測試分支時，網頁第一次執行找不到 YAML

- **症狀**：網頁 New pipeline → Existing YAML 建好定義後按 Run，報「找不到有效的 YAML」。
- **根因**：Run 對話框預設選 repo 的預設分支（正式分支），但 `azure-pipelines.yml` 目前只推到測試分支。
- **解法**：Run 時把分支改成含這個 yaml 的分支；或等 yaml 合進正式分支後再執行。
- **驗證狀態**：已遇到

### 58. 用 REST 建 pipeline 時，yaml 不在 repo 的預設分支就回 500

- **症狀**：`POST _apis/pipelines`（建立 YAML pipeline）回 500，訊息是找不到檔案；yaml 確實存在，只是在另一個分支（例：預設分支是正式分支，CD 的 yaml 只在測試分支）。
- **根因**：這個 API 只到 repo 的**預設分支**找 yaml（與第 28 條網頁 Run 預設選預設分支是同一類問題）。
- **解法**：改用 build definitions API，在 `repository.defaultBranch` 指定含 yaml 的分支，**不必改 repo 的預設分支**：
  `POST {collection}/<專案>/_apis/build/definitions?api-version=6.0`，body
  `{"name":"<名稱>","type":"build","queue":{"id":<佇列 id>},"process":{"type":2,"yamlFilename":"/<yaml 路徑>"},"repository":{"id":"<repo id>","name":"<repo 名稱>","type":"TfsGit","defaultBranch":"refs/heads/<含 yaml 的分支>"}}`。
  佇列 id 用 `GET .../_apis/distributedtask/queues?queueName=Default` 取得。另一個可行但會動到 repo 設定的做法：暫時把 repo 預設分支改成含 yaml 的分支 → 建立 → 改回（另一次操作實際用過）。`scripts/ado-bootstrap-project.sh` 在 pipelines API 回 500 時自動改走 build definitions（腳本的這條路徑只有離線測試）。
- **驗證狀態**：已遇到（500 → 改用 build definitions 一次成功）

### 29. 透過 SSH 推送後，CI 觸發有明顯延遲

- **症狀**：推送到 ADO 後，CI 觸發的建置約 30 分鐘後才出現。
- **根因**：**未釐清**。只觀察到現象，沒有查原因，也不確定每次都會發生。
- **解法**：急著驗證時直接手動排入建置（網頁 Run，或有 Build 執行權限的權杖呼叫 REST API），不要等觸發。
- **驗證狀態**：**未釐清**（單次觀察；延遲時間是觀察值，原因與重現條件都未查）。第五次導入發現另一種會讓人誤以為「推送沒觸發」的查法錯誤（只看已完成的建置，見第 56 條）；本條的觀察是否也是同一原因**未查證**。

### 56. 判斷「推送後 CI 有沒有自動觸發」只看已完成的建置，會誤判成沒觸發

- **症狀**：推送後查建置清單，前幾筆都是舊的，就判定「SSH 推送不會觸發 CI」，再手動排入一次，結果同一個 commit 跑了兩次。
- **根因**：剛觸發的建置還在 `notStarted`／`inProgress`，只看已完成清單（或預設排序的前幾筆）看不到它。
- **解法**：查 `GET .../_apis/build/builds?definitions=<id>&statusFilter=inProgress,notStarted&api-version=6.0`，確認沒有排隊或執行中的建置，才判定沒觸發、才手動排入。
- **驗證狀態**：已遇到（舊 server 曾因此誤判並多排一次重複建置；事後確認推送觸發的建置早已存在）

### 36. 推含 azure-pipelines.yml 的新 repo，ADO 會自動建一條 pipeline

- **症狀**：只是推了一個含 `azure-pipelines.yml` 的 repo（例如臨時探測用），pipeline 清單多出一條 `<repo 名稱> CI`。
- **根因**：ADO 偵測到新 repo 的預設分支有 yaml，自動建立 pipeline 定義。
- **解法**：刪臨時 repo 時一併刪掉這條自動建立的定義（`DELETE _apis/build/definitions/<id>`）；正式 repo 則注意不要因此出現兩條指向同一個 yaml 的 pipeline。
- **驗證狀態**：已遇到

### 37. Server 版不支援 Pipeline Artifacts

- **症狀**：`PublishPipelineArtifact@1` 執行時報「內部部署不支援管線成品工作。請改用組建成品工作。」。這個 task 在 server 的 task 清單裡**查得到**，只有執行時才失敗。
- **根因**：Pipeline Artifacts 只有雲端版 Azure DevOps Services 支援。
- **解法**：stage 之間傳檔改用 `PublishBuildArtifacts@1`（`PathtoPublish`、`ArtifactName`、`publishLocation: Container`）與 `DownloadBuildArtifacts@1`（`buildType: current`、`downloadType: single`、`artifactName`、`downloadPath`；檔案會放在 `<downloadPath>\<artifactName>\`）。
- **驗證狀態**：已驗證（改用 Build Artifacts 後，下一個 stage 成功取得檔案）

### 38. Gradle 版本已在快取清單內，離線建置仍缺套件

- **症狀**：專案的 Gradle 版本已列在 `cached_gradle_versions`，第一次離線建置仍報 `No cached version of <套件> available for offline mode`。
- **根因**：第 25 條處理的是「Gradle 版本」層級；但**各專案自己的依賴**（外掛、函式庫）不一定被同一台 agent 下載過。同版本的其他專案建置過，不代表這個專案的套件都在快取裡。
- **解法**：新專案第一次建置一律連線跑：網頁取消勾選 offline，或 REST 觸發時帶 `templateParameters: {"offline":"false"}`（`scripts/ado-run.sh -p offline=false`）。成功後再用預設的離線模式跑一次，確認之後不用連線也能建置。
- **新 agent 也一樣**（第五次導入）：新增的 agent 第一次建置以 `offline=false` 連線下載 Gradle 與相依，之後以 `offline=true` 再跑一次確認快取可用；兩次都是 1764 個測試、0 失敗。
- **驗證狀態**：已遇到（兩個 repo 都是離線失敗 → 連線成功 → 離線成功；第五次導入的新 agent 連線、離線各一次皆通過）

### 39. 建立或移動部署分支會立刻觸發一次真正的部署

- **症狀**：pipeline 對某分支（例如測試環境的部署分支）設了自動部署；用 REST 建立該分支或把它移到新 commit，就立刻排入預設參數的建置（每條設了自動觸發的 pipeline 各一次）——預設就是正式部署，而你只是想先跑 dry-run。
- **根因**：建立／更新 ref 等同推送，會觸發 CI。
- **解法**：先撤銷 pipeline 對部署金鑰（secure file）的授權 → 建立或移動分支 → 自動觸發的建置立刻取消（〔推測〕即使沒取消到，也會卡在等授權或在下載金鑰時失敗，到不了部署步驟；實例都在部署前取消，沒有觀察到這一段） → 恢復授權 → 手動以 dry-run 參數執行。撤銷與恢復都要讀回確認。詳見 `cd-ssh-vm.md`。
- **驗證狀態**：已遇到（三次建立／移動分支，自動觸發的建置都在部署前取消，部署 stage 為 skipped）

### 59. DownloadBuildArtifacts 的 cleanDestinationFolder 會清掉同一個 job 剛簽出的程式碼

- **症狀**：Deploy job 先 `checkout: self`，再 `DownloadBuildArtifacts@1`（`downloadPath: $(Pipeline.Workspace)`、`cleanDestinationFolder: true`），下一步報找不到 repo 內的路徑。
- **根因**：`cleanDestinationFolder: true` 會清空**整個** `downloadPath`；指向工作區根目錄時，剛簽出到 `s/<repo>` 的程式碼一起被刪。
- **解法**：`downloadPath` 一律用專用子目錄（例：`$(Pipeline.Workspace)\artifacts`，檔案在 `<downloadPath>\<artifactName>\`）。`cleanDestinationFolder: true` 建議保留：自架 agent 的工作區跨 run 保留，〔推測〕不清時以 `Get-ChildItem *.war | Select-Object -First 1` 這類寫法找產物，可能挑到上一版的殘留檔。
- **驗證狀態**：已遇到（改成子目錄後同一條 pipeline 的 dry-run 通過）

## 同步與操作環境

### 16. macOS 會清掉 `/tmp` 裡久未存取的檔案

- **症狀**：放在暫存目錄的 bare clone 失去 `HEAD`、`config`、`packed-refs`，但 objects 還在。
- **根因**：macOS 會定期清除 `/tmp` 底下超過 3 天沒存取的檔案。
- **解法**：同步用的工作 clone 放在持久目錄。`sync-gitea-to-ado.sh` 預設放在 `${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/`。
- **驗證狀態**：已遇到

### 17. zsh 的 `>` 受 noclobber 影響，會沿用舊檔

- **症狀**：迴圈裡每輪寫暫存檔，結果每輪讀到的都是第一輪的內容。
- **根因**：開了 `noclobber` 時，`>` 不會覆寫已存在的檔案，而失敗訊息很容易被忽略。
- **解法**：迴圈寫暫存檔一律用 `>|`。
- **驗證狀態**：已遇到

### 24. 推送保護分支被本機安全機制擋下

- **症狀**：自動化流程推 `main` 時被擋下。
- **根因**：作者環境有 hook，會阻擋 agent 推送保護分支。其他環境未必有這個 hook，但「保護分支由人親自推」本身是合理的做法。
- **解法**：腳本與 skill 遇到保護分支時，不代為推送，只印出可以直接貼上的指令，讓使用者自己執行。
- **驗證狀態**：已遇到（作者環境）

### 31. 用主機名判斷 git 平台會判錯

- **症狀**：依主機名（例如名稱裡有 git、gitlab）推測平台，照著寫的 API 呼叫或同步做法對不上。
- **根因**：主機名是人取的，跟實際跑的軟體沒有關係。
- **解法**：看 API 回應判斷：`curl <主機>/api/v1/version`，Gitea 會回 `{"version":"..."}`，需要登入時也會回 JSON 訊息；`curl <主機>/api/v4/version`，GitLab 回 JSON（未登入是 401 JSON）。`ci-discover.sh` 已照這個順序判斷。
- **驗證狀態**：已遇到

### 43. Wiki 改既有頁面不帶 If-Match，server 回 500

- **症狀**：`PUT _apis/wiki/wikis/<id>/pages?path=...` 新建頁面回 201，但同一個請求拿來改既有頁面回 500。
- **根因**：改既有頁面必須帶 `If-Match: <ETag>`。〔未查證來源〕一般預期缺少時回 412，這台 server 實際回 500，錯誤訊息看不出原因。
- **解法**：先 `GET` 該頁、從回應標頭取 `ETag`，再帶 `If-Match` 送 `PUT`（回 200）。
- **驗證狀態**：已驗證（同一內容不帶 If-Match 回 500、帶了回 200）

### 53. 在 Windows PowerShell 5.1 主控台貼上含中文的指令，中文變亂碼並吃掉引號

- **症狀**：給使用者一段含中文欄位名稱的 PowerShell 指令（例：計算欄位命名為「RAM 總量」），貼到 Windows PowerShell 5.1 互動視窗後中文變亂碼、引號被吃掉，整行解析失敗。
- **根因**：〔推測〕主控台以系統字碼頁處理貼上的文字，與第 41 條「讀無 BOM 的 `.ps1`」同類；主控台這條路徑的細節未另查。
- **解法**：給使用者在 Windows 主控台執行的指令**一律只用 ASCII**：欄位名稱用英文（例：`TotalGB`），說明放在指令外的文字裡。需要中文輸出的邏輯寫成 UTF-8 with BOM 的 `.ps1` 檔再執行。
- **驗證狀態**：已遇到
