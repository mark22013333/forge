# CD：從自架 ADO agent 經 SSH 部署到 VM

本文件來自一個 Tomcat＋war 的實際專案：在 CI（第 4、5 章）跑通之後，再加上「推 ADO 的部署分支 → 自動部署到測試 VM」。
範本放在 `cd-templates/`。**整體 CD 流程的驗證狀態見最後一節，大部分仍是未驗證**，套用時請照實告知使用者。

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

實例的探測結果：TCP 22 可達、SSH 握手成功；agent 內附的是 MinGit，**沒有 bash、unzip、sha256sum、scp**，只有 `C:\Windows\System32\OpenSSH\ssh.exe／scp.exe`。
所以 bash 寫的部署腳本不能直接在 agent 上跑，agent 端改用 PowerShell（`deploy-vm.ps1`）。

### 2. 專用金鑰與主機金鑰

- 產生 CD 專用、無密碼的金鑰（ed25519 即可，這是連 VM 用的，不是 ADO 內建 SSH）；公鑰加到 VM 帳號的 `authorized_keys`（先備份、不重複加入），用新金鑰登入測試一次
- 主機金鑰**釘選成檔案**放進 repo（例：`ci/<環境>_known_hosts`）：`ssh-keyscan` 取得後，和筆電上已信任的 known_hosts 指紋比對一致才寫入；
  pipeline 用 `StrictHostKeyChecking=yes` 驗證，**不要用 `StrictHostKeyChecking=no`**

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
- 參數：`deployDryRun`（只做預檢，不上傳、不動服務）；產品專屬的保護參數（例：允許新產物少類別）視專案加

### 5. 建立部署分支（撤銷授權技巧）

建立或移動部署分支**本身就會觸發 CD**，而且預設是正式部署。第一次建立、或還在測試 pipeline 時：

1. 撤銷 pipeline 對部署金鑰（secure file）的授權，讀回確認清單為空
2. 用 REST 建立／移動部署分支（`POST .../_apis/git/repositories/<repo>/refs`，帶 `oldObjectId`）
3. 自動觸發的那次會在需要金鑰時卡住 → 立刻取消（`PATCH .../_apis/build/builds/<id>`，`{"status":"cancelling"}`）
4. 恢復授權、讀回確認
5. 手動以 `templateParameters: {"deployDryRun":"true"}` 執行（`scripts/ado-run.sh ... -p deployDryRun=true`）

實例：這樣取消的建置，部署 stage 顯示 skipped，服務完全沒被動到。

### 6. dry-run → 正式部署

- dry-run 通過後，**正式部署前要再向使用者確認**（服務會短暫停機）
- 日常觸發：`cd-templates/push-deploy-branch.sh`——確認本機沒有未推上 Gitea 的 commit → 依賴 repo 快轉 → 主 repo 快轉 → 移動部署分支
- 同一個 commit 推到 ADO 正式分支與部署分支，會各觸發一次建置（CI 一次、CD 一次）

## Windows PowerShell 5.1 陷阱（agent 端腳本）

agent 的 PowerShell task 預設用 Windows PowerShell 5.1：

| 陷阱 | 症狀 | 做法 |
|------|------|------|
| `.ps1` 沒有 BOM | 以系統 ANSI（例：Big5）解讀，中文變亂碼並吃掉引號，整支腳本 `Unexpected token` | 存成 **UTF-8 with BOM**，檔頭註明 |
| `ErrorActionPreference=Stop` | 原生程式（ssh／scp）一寫 stderr 就變例外中止 | 呼叫原生程式的函式內暫改為 `Continue`，以 `$LASTEXITCODE` 判斷 |
| 管線傳字串給原生程式 | 依 `$OutputEncoding` 轉成 ASCII，中文變 `?` | 不用管線；先把腳本寫成檔案 scp 上去，再 ssh 執行 |
| CRLF | checkout 可能是 CRLF，bash 遇 `\r` 出錯 | 送到 VM 前轉 LF、UTF-8 無 BOM 寫出 |
| 私鑰權限 | Windows OpenSSH 拒絕其他帳號可讀的私鑰 | 複製一份後 `icacls <key> /inheritance:r /grant:r "<whoami>:R"` |
| scp 逾時 | scp 會再開 ssh 子程序，只 kill scp 會留下子程序 | `taskkill /T /F /PID <pid>` 結束整棵程序樹 |

**推送前的本機檢查**（省一輪建置）：下載官方 PowerShell 7 tarball（免安裝），以「有 BOM 讀 UTF-8、否則以 Big5 讀」模擬 PS 5.1 讀檔，再用
`[System.Management.Automation.Language.Parser]::ParseInput` 解析；先拿無 BOM 的舊版做正對照，確認模擬抓得到錯誤。

## 遠端腳本契約

`deploy-vm.ps1` 只負責傳輸與追蹤，VM 上的實際動作由專案自己的遠端腳本（bash）負責。契約：

- 呼叫方式：`bash <遠端腳本> deploy <staged 產物路徑> <sha256> <commit>`；另需支援唯讀的 `precheck`（輸出 `key=value`，至少 `locked`、`disk_avail_kb`）
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
| 授權／撤銷 pipelinePermissions | 實例中既有權杖即可（建專案用的範圍已涵蓋），未另做範圍對照 |
| 建立／移動部署分支 | Code：Read & write（或以 SSH 金鑰推送） |

## 驗證狀態

| 項目 | 狀態 |
|------|------|
| 連線探測 pipeline（`probe-agent-ssh.yml`） | **已在實際專案跑過**：TCP 22 與 SSH 握手確認可達；本檔為去識別化版 |
| Build Artifacts 傳遞產物（Server 版不支援 Pipeline Artifacts） | **已驗證**：Pipeline Artifacts 實跑報錯，改 Build Artifacts 後 war 成功傳到 Deploy stage |
| secure file 上傳、授權、撤銷授權技巧 | 已遇到：實際專案操作成功，取消的建置部署 stage 為 skipped |
| `deploy-vm.ps1` 與整體 CD | **未驗證**：實際專案的 dry-run 第一次因 Pipeline Artifacts、第二次因 `.ps1` 沒有 BOM 失敗；修正版 dry-run 尚未跑、也未正式部署。本骨架另經去識別化改寫，同樣未在 ADO 執行過 |
| `push-deploy-branch.sh` | 未驗證：實際專案的版本只跑過預覽與「本機有未推送 commit 時中止」，未以 `--apply` 實際推送；本檔為參數化改寫 |
