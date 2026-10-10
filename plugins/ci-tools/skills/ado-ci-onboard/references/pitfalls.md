# 踩坑表：自架 Azure DevOps Server 導入 CI

這 43 條都是實際導入時遇到的問題，不是從文件整理來的。每條都列症狀、根因、解法、驗證狀態。

第 1～24 條來自第一個導入的專案；第 25～33 條來自第二個專案（Gradle 8.14.3、主 repo＋依賴 repo，接在已經裝好的同一台 server 上）；第 34～43 條來自第三個專案（Gradle 7.5.1、主 repo＋依賴 repo，另有一個獨立 repo，接在同一台 server 上，並做了從 Windows agent 經 SSH 部署到 Linux VM 的 CD）。

## 驗證狀態的意思

| 狀態 | 意思 |
|------|------|
| **已驗證** | 有對照實驗或重現證據支撐根因與解法 |
| **已遇到** | 實際發生過，解法當下有效；根因沒有另外做對照實驗證明 |
| **估計** | 數字是推估，沒有量測過 |
| **未釐清** | 現象確定存在，原因還不知道 |

引用這份表時，請保留狀態標註，不要把「估計」或「未釐清」寫成肯定句。

實測環境：ADO Server Express＋SQL Server 2025 Express（具名執行個體），裝在 Windows 11 專業版工作站。這個 OS 組合不在官方支援清單內（見第 21 條）。

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
- **驗證狀態**：已遇到（唯讀權杖讀取正常、寫入 401；「各動作對應哪個範圍」是依官方範圍名稱整理，未逐一做對照實驗）

### 34. 用 REST 建 pipeline 回 401，但 pipeline 其實已經建好

- **症狀**：`POST _apis/pipelines` 回 401（或回應不是 JSON），看起來像建立失敗。
- **根因**：權杖缺 Build：讀取與執行時，定義本身已建立，401 來自建立之後的後續動作（推測，未查證是哪一步）。
- **解法**：**不要直接重送**，先 `GET _apis/pipelines` 列出確認；已存在就讀回它的 `configuration` 檢查 yaml 路徑與 repo id。重送會建出名稱重複的第二條。
- **驗證狀態**：已遇到（兩條 pipeline 都出現同樣情況，列表確認皆已建成；根因未做對照實驗）

### 35. 新專案的 pipeline 一直停在 notStarted，agent 卻閒著

- **症狀**：手動觸發成功（HTTP 200、拿到 run id），但建置永遠 `notStarted`；agent 在線且閒置，agent pool 的排隊清單裡也看不到這個工作。
- **根因**：新專案的 pipeline 還沒被授權使用資源：agent 佇列（`queue`）、yaml 裡 `resources.repositories` 引用的其他 repo（`repository`，id 為 `<專案 id>.<repo id>`）、`DownloadSecureFile` 用到的 secure file（`securefile`）。網頁上會出現「需要權限」的 Permit 按鈕，REST 觸發時看不到。
- **解法**：`GET _apis/pipelines/pipelinePermissions/<type>/<id>?api-version=6.1-preview.1` 看授權清單（空的就是原因），`PATCH` 同一路徑、body `{"pipelines":[{"id":<pipeline id>,"authorized":true}]}`，再讀回確認。可用 `scripts/ado-authorize.sh`。
- **驗證狀態**：已驗證（授權清單為空 → PATCH 後同一次建置隨即開始執行）

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
- **解法**：同一台機器多裝幾個 agent，各自使用不同的資料夾與名稱。記憶體以每個建置 6～8GB 粗估，先開 3～4 個再觀察。會改資料的 E2E（例如 Playwright）不可並行，要放獨立的 pool，或用 `lockBehavior: sequential`。
- **驗證狀態**：**估計**（6～8GB 是推估值，沒有量測過）。兩個 agent 並行有實際重疊 5 分鐘且都成功，但這不是完整的壓力測試。

### 40. agent 內附的 git 是 MinGit，沒有 bash 與常用工具

- **症狀**：想在 pipeline 裡直接跑既有的 bash 部署腳本，但 agent 上找不到 bash。
- **根因**：agent 的 `externals\git` 是精簡版 MinGit：有 `ssh.exe`、`awk`、`sed`、`mktemp`，**沒有** `bash.exe`、`scp`、`unzip`、`sha256sum`、`stat`、`dd`。
- **解法**：Windows agent 上的部署邏輯改用 PowerShell 5.1，連線用 Windows 內建的 `C:\Windows\System32\OpenSSH\ssh.exe`／`scp.exe`；需要在目標機執行的部分仍可用 bash（把腳本 scp 過去再執行）。見 `cd-ssh-vm.md`。
- **驗證狀態**：已驗證（以探測 pipeline 逐一檢查工具是否存在）

### 41. Windows PowerShell 5.1 讀無 BOM 的 .ps1，中文變亂碼、整支腳本無法解析

- **症狀**：repo 裡的 `.ps1` 在 agent 上執行時報一連串 `Unexpected token '}'`、`Missing closing ')'`，錯誤訊息裡的中文是亂碼。同一支腳本在 PowerShell 7 上正常。
- **根因**：Windows PowerShell 5.1 讀取沒有 BOM 的檔案時，用系統 ANSI 字碼頁（繁中 Windows 為 Big5）解讀，UTF-8 的中文位元組被拆錯，吃掉了後面的引號。
- **解法**：repo 內給 Windows PowerShell 5.1 執行的 `.ps1` 一律存成 **UTF-8 with BOM**，並在檔頭註明。推送前可用 PowerShell 7（官方 tarball 免安裝）以「有 BOM 讀 UTF-8、否則讀 Big5」模擬 5.1 解析，省下一輪建置。yaml 內嵌的 `powershell:` 腳本不受影響。
- **驗證狀態**：已驗證（以 Big5 模擬讀取重現 16 個解析錯誤，加 BOM 後 0 個；加 BOM 後在 agent 上的實際結果尚待確認）

### 42. PowerShell 在 ErrorActionPreference=Stop 時，原生程式寫 stderr 就中止

- **症狀**：腳本呼叫 `ssh.exe` 等原生程式，只是印出一行警告（例如 `Warning: Permanently added ... to the list of known hosts`），整個步驟就以 `NativeCommandError` 失敗。
- **根因**：Windows PowerShell 5.1 在 `2>&1` 重導或 `$ErrorActionPreference = 'Stop'` 時，把原生程式的 stderr 輸出當成錯誤紀錄處理。
- **解法**：在包裝原生程式的函式內暫時設 `$ErrorActionPreference = 'Continue'`，成敗改看 `$LASTEXITCODE`；ssh 另加 `-o LogLevel=ERROR` 減少雜訊。
- **驗證狀態**：已遇到（探測 pipeline 的連線本身成功，步驟卻因這行警告失敗）

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

### 29. 透過 SSH 推送後，CI 觸發有明顯延遲

- **症狀**：推送到 ADO 後，CI 觸發的建置約 30 分鐘後才出現。
- **根因**：**未釐清**。只觀察到現象，沒有查原因，也不確定每次都會發生。
- **解法**：急著驗證時直接手動排入建置（網頁 Run，或有 Build 執行權限的權杖呼叫 REST API），不要等觸發。
- **驗證狀態**：**未釐清**（單次觀察；延遲時間是觀察值，原因與重現條件都未查）

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
- **驗證狀態**：已遇到（兩個 repo 都是離線失敗 → 連線成功 → 離線成功）

### 39. 建立或移動部署分支會立刻觸發一次真正的部署

- **症狀**：pipeline 對某分支（例如測試環境的部署分支）設了自動部署；用 REST 建立該分支或把它移到新 commit，就立刻排入一次預設參數的建置——預設就是正式部署，而你只是想先跑 dry-run。
- **根因**：建立／更新 ref 等同推送，會觸發 CI。
- **解法**：先撤銷 pipeline 對部署金鑰（secure file）的授權 → 建立或移動分支 → 自動觸發的那次會卡在等授權（或在下載金鑰時失敗），把它取消 → 恢復授權 → 手動以 dry-run 參數執行。撤銷與恢復都要讀回確認。詳見 `cd-ssh-vm.md`。
- **驗證狀態**：已遇到（三次建立／移動分支，自動觸發的建置都在部署前取消，部署 stage 為 skipped）

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
- **根因**：改既有頁面必須帶 `If-Match: <ETag>`。官方文件說缺少時回 412，這台 server 實際回 500，錯誤訊息看不出原因。
- **解法**：先 `GET` 該頁、從回應標頭取 `ETag`，再帶 `If-Match` 送 `PUT`（回 200）。
- **驗證狀態**：已驗證（同一內容不帶 If-Match 回 500、帶了回 200）
