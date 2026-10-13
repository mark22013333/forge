# CD：從自架 ADO agent 經 SSH 部署到 VM

本文件來自一個 Tomcat＋war 的實際專案：在 CI（第 4、5 章）跑通之後，再加上「推 ADO 的部署分支 → 自動部署到測試 VM」。
範本放在 `cd-templates/`。**整體 CD 流程的驗證狀態見最後一節**：來源專案的實作已跑通 dry-run 與正式部署，但本目錄的範本是去識別化改寫版、沒有在 ADO 執行過，套用時請照實告知使用者。

## 適用條件

- agent 是 Windows 自架 agent（NetworkService 執行），目標 VM 是 Linux、可用 SSH 金鑰登入
- 部署 = 換 war／jar 等單一產物並重啟服務；VM 上的停止、備份、切換、健康檢查、回滾由專案自己的**遠端腳本**負責
- 主要 repo 在 Gitea，**只能經 VPN 存取、不設 push mirror**：CD 的觸發點只能放在 ADO
- 不適用：需要多台機器滾動部署、容器編排、或 VM 不允許金鑰登入

## 架構

```
本機 ──push──▶ Gitea feature 分支（主要 repo，CD 不碰）
本機 ──一鍵腳本──▶ ADO 正式分支（快轉同步）──▶ ADO 部署分支（例：SIT，只存在 ADO）
                                                   │ 觸發
                                       Build stage：建置＋PublishBuildArtifacts
                                                   │
                                       Deploy stage：DownloadBuildArtifacts＋DownloadSecureFile
                                                   │ deploy-vm.ps1（PowerShell 5.1＋內建 OpenSSH）
                                                   ▼
                                       VM：setsid nohup 執行遠端腳本 → result 檔
```

- 部署分支只建在 ADO，Gitea 完全不動；部署分支只是「部署指令」，不放新程式碼
- 部署分支每次都從正式分支快轉，**不 force push**

## 步驟

### 1. 連線探測（先確認 agent 到得了 VM）

agent 主機的網路與你的筆電不同（實例中走另一張虛擬網卡）。先用 `cd-templates/probe-agent-ssh.yml` 跑一次：
- 建一個**臨時 repo**（只在 ADO），用 REST `pushes` API 推入探測 yaml，建 pipeline、授權佇列、執行
- 探測內容：`Test-NetConnection <VM> -Port 22`、列出 agent 上可用的工具、不帶金鑰的 ssh 握手
  （看到收到主機金鑰／`Permission denied` 就代表 SSH 服務可達，**不需要放任何金鑰**）
- 探完刪除 pipeline 與 repo。注意：推入含 `azure-pipelines.yml` 的新 repo 時，ADO 會**自動多建一條** `<repo> CI` pipeline，也要一起刪
- 探測步驟可能顯示失敗：ssh 把「已加入 known hosts」的警告寫到 stderr，PowerShell 視為錯誤。看輸出內容判斷，不要只看結果

實例的探測結果：TCP 22 可達、SSH 握手成功；agent 內附的是 MinGit，**沒有 bash、unzip、sha256sum、scp**（有 `usr\bin\ssh.exe`）；系統另有 `C:\Windows\System32\OpenSSH\ssh.exe／scp.exe`，部署腳本用這一組。
所以 bash 寫的部署腳本不能直接在 agent 上跑，agent 端改用 PowerShell（`deploy-vm.ps1`）。

金鑰、主機金鑰釘選檔、secure file 都備好後（第 2、3 步），再用 `cd-templates/probe-agent-ssh-key.yml` 做第二階段探測：
DownloadSecureFile → 私鑰複製到 `$(Agent.TempDirectory)` → `icacls /inheritance:r /grant:r` → ssh 以
`-o StrictHostKeyChecking=yes -o UserKnownHostsFile=<repo 內的釘選檔>` 登入，只跑 `echo ok; whoami; ls -ld <專案根目錄>`。
這一步通過，就證明「agent 帳號＋這把金鑰＋這個釘選檔」能登入（第五次導入實測通過）。它要另寫一支，因為 yaml 一引用 secure file，
即使步驟不執行也會在驗證階段檢查（踩坑 45）。

### 2. 專用金鑰與主機金鑰

- 產生 CD 專用、無密碼的金鑰（ed25519 即可，這是連 VM 用的，不是 ADO 內建 SSH）
- 公鑰加到 VM 帳號的 `authorized_keys`（第五次導入實際照這個順序做過）：
  1. 備份：`cp -p ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak-$(date +%Y%m%d%H%M%S)`
  2. 防重複：先 `grep -F` 公鑰本體，已存在就不加
  3. 追加一行，前綴限制：`no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... <註解>`
  4. 確認既有的行沒被動到：`diff` 新檔扣掉最後一行與備份檔應完全相同
  5. 檢查權限是 `600`，有 SELinux 的主機用 `ls -Z` 確認標籤是 `ssh_home_t`
  6. 用新金鑰登入測試，排除 agent 與設定檔裡的其他金鑰：
     `ssh -F /dev/null -o IdentitiesOnly=yes -o IdentityAgent=none -i <新私鑰> <帳號>@<VM> 'echo ok'`
- 主機金鑰**釘選成檔案**放進 repo（例：`ci/<環境>_known_hosts`）：`ssh-keyscan` 取得後，和筆電上已信任的 known_hosts 指紋比對一致才寫入；
  keyscan 的輸出含 `#` 開頭的註解行，寫入前濾掉（`ssh-keyscan -p 22 <VM> 2>/dev/null | grep -v '^#'`）。比對指紋：`ssh-keygen -lf <檔案>`。
  pipeline 用 `StrictHostKeyChecking=yes` 驗證，**不要用 `StrictHostKeyChecking=no`**
- **先查 VM 上 `sudo -n true` 是否免密碼**：可以的話，CD 不需要把 sudo 密碼放進 ADO（少一個機密）；部署腳本與預檢都只用 `sudo -n`，
  需要密碼時直接失敗，不會卡在等輸入。不能免密碼時先跟使用者討論，不要把密碼塞進 pipeline 變數了事

### 3. Secure file 上傳與授權

- 私鑰上傳到 ADO Library → Secure files：
  `POST {url}/{collection}/{project}/_apis/distributedtask/securefiles?name=<檔名>&api-version=6.0-preview.1`（`application/octet-stream`）
- 權杖要有 **Secure Files：Read, create & manage**。只有讀取範圍時「查得到清單、上傳回 401」
- 授權 pipeline 使用：`PATCH .../_apis/pipelines/pipelinePermissions/securefile/<id>?api-version=6.1-preview.1`，
  body `{"pipelines":[{"id":<pipeline id>,"authorized":true}]}`，寫完 GET 讀回確認（可用 `scripts/ado-authorize.sh`）
- 新專案的 pipeline 也要授權 agent 佇列（`queue/<佇列 id>`）與被引用的 repo（`repository/<專案id>.<repoId>`），否則建置永遠 `notStarted`、agent 卻閒置

### 4. 兩 stage pipeline

- Build stage 最後加 `PublishBuildArtifacts@1`（只在部署分支執行）；Deploy stage 用 `DownloadBuildArtifacts@1`＋`DownloadSecureFile@1`＋呼叫 `deploy-vm.ps1`。
  片段見 `cd-templates/cd-stage-snippet.yml`
- **ADO Server（自架）不支援 Pipeline Artifacts**：`PublishPipelineArtifact` 在 task 清單查得到，執行時才報「內部部署不支援管線成品工作。請改用組建成品工作。」
- `DownloadBuildArtifacts@1` 的 `downloadPath` **一定要用專用子目錄**（例：`$(Pipeline.Workspace)\artifacts`）：`cleanDestinationFolder: true`
  會清空整個 downloadPath，指向工作區根目錄時連同同一個 job 剛簽出的程式碼一起刪掉（踩坑 59）
- 只能從特定分支部署時（例：測試環境只部署測試分支），Build stage 第一步就檢查 `Build.SourceBranch`，不符就失敗，
  不要等到 Deploy stage 的 condition 才跳過——手動執行時選錯分支會白跑一整輪建置
- 參數：`deployDryRun`（只做預檢，不上傳、不動服務）；產品專屬的保護參數（例：允許新產物少類別）視專案加

### 5. 建立部署分支（撤銷授權技巧）

建立或移動部署分支**本身就會觸發 CD**，而且預設是正式部署。第一次建立、或還在測試 pipeline 時：

1. 撤銷 pipeline 對部署金鑰（secure file）的授權，讀回確認清單為空
2. 用 REST 建立／移動部署分支（`POST .../_apis/git/repositories/<repo>/refs`，帶 `oldObjectId`）
3. 自動觸發的建置立刻取消（〔推測〕沒取消到也會卡在等授權、到不了部署步驟；實例都在部署前取消）（`PATCH .../_apis/build/builds/<id>`，`{"status":"cancelling"}`）
4. 恢復授權、讀回確認
5. 手動以 `templateParameters: {"deployDryRun":"true"}` 執行（`scripts/ado-run.sh ... -p deployDryRun=true`）

實例：這樣取消的建置，部署 stage 顯示 skipped，服務完全沒被動到。

### 6. dry-run → 正式部署

- dry-run 通過後，**正式部署前要再向使用者確認**（服務會短暫停機）
- 日常觸發：`cd-templates/push-deploy-branch.sh`——確認本機沒有未推上 Gitea 的 commit → 依賴 repo 快轉 → 主 repo 快轉 → 移動部署分支
- 同一個 commit 推到 ADO 正式分支與部署分支，會各觸發一次建置（CI 一次、CD 一次）

## 遠端唯讀預檢：腳本經 stdin 送入，VM 零落地

dry-run 的承諾是「不動 VM」，所以預檢腳本也不要上傳到 VM：

- agent 把預檢 bash 腳本讀成**位元組**（先轉 LF、去 BOM），以 `ProcessStartInfo` 寫進 ssh 的 stdin，遠端執行
  `bash -s -- <參數...>`。VM 上不落地任何檔案，也避開 PowerShell 5.1 管線依 `$OutputEncoding` 把中文轉成 `?` 的問題。
  `deploy-vm.ps1` 的 `Invoke-SshStdin` 是這個寫法。第五次導入的實際專案以同樣的 stdin 送入方式跑通 dry-run，但它送的是
  **專用的唯讀預檢腳本**；範本送的是「常數＋整支遠端腳本」，兩者只共用送入方式
- 經 `bash -s` 執行時 `$0` 是 `bash`、`BASH_SOURCE` 是空的，沒有腳本檔路徑：預檢路徑**不得依賴** `$0`、`$(dirname "$0")` 等腳本所在目錄
- 腳本本身從 stdin 讀進來，所以腳本裡**任何會讀 stdin 的子程序**（`sudo`、`docker`、`curl`、`flock`…）都要加 `< /dev/null`，
  否則它們會吃掉後面還沒執行的腳本內容
- 預檢只做讀取：`test`、`grep`、`df`、`sha256sum`、`sudo -n true`、`sudo -n -l <指令>`（只列權限）、`docker inspect` 等；
  鎖檔只在已存在時以唯讀開啟測一下共享鎖，不存在就不碰（不要因為預檢而建立鎖檔）
- 預檢輸出由 agent 端解析判定，格式以「遠端腳本契約」為準：一行一個 `key=value`、以 `MISSING_`／`MISMATCH` 開頭的行一律視為失敗、
  最後印 `precheck_done=1`，沒看到就當作預檢沒跑完（`deploy-vm.ps1` 照這三條檢查）。參考專案自己的預檢用的是 `KEY 值` 格式與
  `PRECHECK_DONE` 標記，屬該專案的慣例；換格式時 agent 端的解析要一起改
- **驗證 dry-run 真的零寫入**：記下執行前的時間，跑完後在 VM 上 `find <專案根目錄> -newermt '<執行前時間>'`，應該沒有輸出；
  要排除服務本身持續寫入的檔案（實例：Quartz 內嵌資料庫 `quartz.mv.db`），不要把它當成 dry-run 寫的（第五次導入以此確認零寫入）

## 沿用既有的遠端部署腳本（包裝模式）

專案已經有一支在 VM 上跑得好好的部署腳本（例：本機部署流程用的 `deploy-remote.sh`），CD 不要改它，而是包一層：

1. 上傳到 `<staging 根目錄>/<TS>/`（目錄 700），檔案清單的 sha256 由 agent 本機計算，寫成 `SHA256SUMS` 一起上傳，
   在 VM 執行 `sha256sum --quiet -c SHA256SUMS`（scp 回傳 0 不代表檔案完整）
2. 包裝腳本放在 `<TS>/` **之外**（例：`<staging 根目錄>/ado-<TS>.run.sh`），log、結果檔、pid 檔也放在外面：
   原腳本成功時常會刪掉自己的 staging 目錄，放在裡面就連結果一起消失
3. agent 以 `setsid nohup bash <包裝腳本> run ... < /dev/null >> <log> 2>&1 &` 在 VM 背景執行，ssh 立刻返回；agent 斷線不影響部署
4. 包裝腳本內以 `sudo -n flock -n -E 75 <鎖檔> bash <TS>/deploy-remote.sh <參數>` 呼叫原腳本：
   `-n` 拿不到鎖立即失敗、`-E 75` 讓「拿不到鎖」和原腳本自己的結束碼分得開；結束碼先寫暫存檔再改名成結果檔，agent 讀到時內容一定完整。
   舊版 util-linux 的 flock 沒有 `-E`（`--conflict-exit-code`），預檢要先檢查（例：`flock --help 2>&1 | grep -q -- --conflict-exit-code`，參考專案的預檢回報 `FLOCK_E yes/no`）
5. agent 每隔一段時間以 `poll <已讀行數>` 取回結果檔狀態與新增的 log 行，印到 ADO log；逾時只代表 ADO 不再等，VM 上的部署仍繼續
6. **umask**：包裝腳本若為了保護 log 設了 `umask 077`，呼叫 `sudo flock` 前要改回 `umask 022`。〔推測〕umask 可能經 sudo 傳給 flock，
   鎖檔第一次被以 root `0600` 建立，之後一般帳號跑的預檢讀不到它；預檢因此要把「讀不到」和「被持有」分開回報
   （範本契約的值是 `locked=unreadable` 與 `locked=1`，兩者都會讓 `deploy-vm.ps1` 中止正式部署），不要把讀不到誤判成有部署在跑。是否真的會發生取決於 sudo 是否經 pam_umask 套用自己的 umask，容器實驗未重現
7. 同一台 VM 若還有其他部署途徑（例：開發者本機直接跑部署腳本）沒有取同一把鎖，兩條路徑之間**不互斥**，要在文件裡寫明

驗證狀態：第五次導入的實作只在 ADO 上跑過 dry-run（預檢經 stdin、零寫入已確認）；包裝腳本的正式部署路徑只在 Linux 容器內以假的
sudo／docker 模擬執行過，**未在 ADO 上做過正式部署**。

## Windows PowerShell 5.1 陷阱（agent 端腳本）

agent 的 PowerShell task 預設用 Windows PowerShell 5.1：

| 陷阱 | 症狀 | 做法 | 驗證狀態 |
|------|------|------|----------|
| `.ps1` 沒有 BOM | 以系統 ANSI（例：Big5）解讀，中文變亂碼並吃掉引號，整支腳本 `Unexpected token` | 存成 **UTF-8 with BOM**，檔頭註明 | 部分驗證（踩坑 41） |
| `ErrorActionPreference=Stop` | 原生程式（ssh／scp）一寫 stderr 就變例外中止 | 呼叫原生程式的函式內暫改為 `Continue`，以 `$LASTEXITCODE` 判斷 | 已遇到（踩坑 42） |
| 管線傳字串給原生程式 | 依 `$OutputEncoding` 轉成 ASCII，中文變 `?` | 不用管線：要送進 stdin 的內容以位元組寫進 `ProcessStartInfo` 的 StandardInput（`deploy-vm.ps1` 的 `Invoke-SshStdin`）；或先寫成檔案 scp 上去再 ssh 執行 | 未驗證（依 PowerShell 5.1 的 `$OutputEncoding` 行為預防）；位元組寫 stdin 的做法已在第五次導入跑通 dry-run |
| 沒設主控台輸出編碼 | 〔推測〕ADO log 裡原生程式輸出的中文可能變 `?` | 腳本開頭設 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`（`deploy-vm.ps1` 已有；第五次導入的實作漏掉，被審查抓到） | 未驗證（預防） |
| CRLF | checkout 可能是 CRLF，bash 遇 `\r` 出錯 | 送到 VM 前轉 LF、UTF-8 無 BOM 寫出 | 未驗證（預防） |
| 私鑰權限 | Windows OpenSSH 拒絕其他帳號可讀的私鑰 | 複製一份後 `icacls <key> /inheritance:r /grant:r "<whoami>:R"` | 未驗證（預防） |
| `ConnectTimeout` | Windows 版 OpenSSH 8.1 設了它，每次連線都等滿這個秒數才成功，分段上傳慢十倍以上 | ssh／scp 不設 `ConnectTimeout`，逾時改在外層處理 | 已驗證（踩坑 44） |
| scp 逾時 | scp 會再開 ssh 子程序，只 kill scp 會留下子程序 | `taskkill /T /F /PID <pid>` 結束整棵程序樹 | 未驗證（預防） |

**推送前的本機檢查**（省一輪建置）：下載官方 PowerShell 7 tarball（免安裝），以「有 BOM 讀 UTF-8、否則以 Big5 讀」模擬 PS 5.1 讀檔，再用
`[System.Management.Automation.Language.Parser]::ParseInput` 解析；先拿無 BOM 的舊版做正對照，確認模擬抓得到錯誤。

## 遠端腳本契約

`deploy-vm.ps1` 只負責傳輸與追蹤，VM 上的實際動作由專案自己的遠端腳本（bash）負責。契約：

- 呼叫方式：`bash <遠端腳本> deploy <staged 產物路徑> <sha256> <commit>`；另需支援唯讀的 `precheck`
- `precheck` 的輸出：一行一個 `key=value`，至少 `locked`、`disk_avail_kb`，**最後一行** `precheck_done=1`；環境不符時印以 `MISSING_` 或 `MISMATCH` 開頭的行。
  `deploy-vm.ps1` 沒看到 `precheck_done=1`、或看到任何 `MISSING_`／`MISMATCH` 行，就中止
- `locked` 的值：`0` 未被持有、`1` 被持有、`unreadable` 鎖檔存在但讀不到。只有 `0` 會繼續；`1`、`unreadable` 與其他任何值都中止（fail-closed）
- `precheck` 由 agent 以 `bash -s -- precheck` 執行、腳本內容從 stdin 送入（VM 不落地）：precheck 路徑裡會讀 stdin 的子程序都要加 `< /dev/null`，
  且**不得依賴** `$0`／`BASH_SOURCE`／腳本所在目錄（此時 `$0` 是 `bash`；`deploy` 仍以檔案執行，兩種模式的 `$0` 不同）
- 由 agent 以 `setsid nohup` 背景啟動，stdout／stderr 寫到 run log；**agent 斷線不影響遠端繼續執行**
- 結束前把一個字寫進環境變數 `RESULT_FILE` 指定的檔案：

| 結果 | 意思 | deploy-vm.ps1 結束碼 |
|------|------|------|
| `OK` | 部署成功、健康檢查通過 | 0 |
| `ROLLED_BACK` | 新版失敗，已自動回滾到上一版 | 2 |
| `ABORTED` | 中止，服務未變動 | 3 |
| `MANUAL` 或其他 | 需人工介入（回滾也失敗等） | 4 |

- 實例（Tomcat＋war）的遠端腳本做了：flock 防重入、只停 `-Dcatalina.base=<本服務>` 的 java 程序（同機另一套 Tomcat 不受影響）、
  systemd unit 為 active 時先等 ExecStop 收尾再啟動、舊版整包移到備份目錄、log 啟動失敗字樣＋HTTP 200 健康檢查、失敗自動回滾、寫部署紀錄
- 停服務前先查 `systemctl list-units --all`、`crontab -l`、`/var/log/cron`：supervisor（systemd ExecStop、cron 定期重啟、watchdog）會把「程序死亡」當觸發條件

## 權杖範圍（CD 會用到的）

| 動作 | 範圍 |
|------|------|
| 觸發建置（含 dry-run） | Build：Read & execute |
| 上傳 secure file | Secure Files：Read, create & manage |
| 授權／撤銷 pipelinePermissions | **未驗證**是哪個範圍（實例中權杖已含多個範圍，未另做對照；與 SKILL.md 權杖表一致） |
| 建立／移動部署分支 | Code：Read & write（或以 SSH 金鑰推送） |

## 驗證狀態

| 項目 | 狀態 |
|------|------|
| 連線探測 pipeline（`probe-agent-ssh.yml`） | **已在實際專案跑過**：TCP 22 與 SSH 握手確認可達；本檔為去識別化版 |
| Build Artifacts 傳遞產物（Server 版不支援 Pipeline Artifacts） | **已驗證**：Pipeline Artifacts 實跑報錯，改 Build Artifacts 後 war 成功傳到 Deploy stage |
| secure file 上傳、授權、撤銷授權技巧 | 已遇到：實際專案操作成功，取消的建置部署 stage 為 skipped |
| 來源專案的整體 CD（agent 端 ps1＋遠端腳本） | **已在實際專案跑通**：dry-run 前兩次分別因 Pipeline Artifacts、`.ps1` 沒有 BOM 失敗，修正後通過；之後兩個 repo（依賴順序：先被依賴的服務）各正式部署成功一次，VM 端另行確認新 JVM 啟動、HTTP 200、另一個服務未受影響。上傳過慢的問題見踩坑 44 |
| `deploy-vm.ps1` 骨架 | **未驗證**：由上述已跑通的實作去識別化、參數化改寫，改寫後的本檔未在 ADO 執行過。預檢改為經 stdin 送入、dry-run 不寫 VM 的修改（依第五次導入的做法）只在本機 PowerShell 7 做過解析（模擬 5.1 讀檔）與 `Invoke-SshStdin` 的函式測試 |
| 預檢經 stdin 送入 `bash -s`、dry-run 零寫入 | **已在實際專案跑通**（第五次導入）：ADO 上 dry-run 通過，事後在 VM 以 `find -newermt` 確認除服務自身持續寫入的檔案外沒有新檔 |
| `probe-agent-ssh-key.yml`（帶部署金鑰登入） | 同結構已在第五次導入跑通；本檔為去識別化版，`sudo -n` 一項為本檔新增 |
| 包裝既有遠端腳本（`setsid nohup`＋`sudo -n flock -n -E 75`） | **未在 ADO 上正式部署過**：只在 Linux 容器內以假的 sudo／docker 模擬；umask 那一項為〔推測〕，未重現 |
| `push-deploy-branch.sh` | 未驗證：實際專案的版本只跑過預覽與「本機有未推送 commit 時中止」，未以 `--apply` 實際推送（該專案的正式部署是以 REST 直接移動部署分支觸發）；本檔為參數化改寫 |
