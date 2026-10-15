---
name: ado-ci-onboard
description: 引導把自架 Azure DevOps Server（ADO）接上既有 Gitea 專案的 CI：先探索既有設定、評估與安裝 Server 與 agent、建立 ADO 專案與 repo、套 pipeline 範本、以 REST API 驗證建置、首次建置失敗分類、Gitea → ADO 單向同步（含多 repo）、測試分層，以及從 Windows agent 經 SSH 部署到 VM 的 CD。當使用者提到「ado-ci-onboard」、「導入 CI」、「自架 Azure DevOps」、「ADO Server」、「Gitea 同步到 ADO」、「pipeline 範本」、「CI 踩坑」、「ADO 部署到 VM」、「ADO CD」時觸發此 Skill。
---

# ADO CI Onboard — 自架 Azure DevOps Server 導入 CI

把既有 Gitea 專案接上自架 Azure DevOps Server（以下簡稱 ADO）的 CI。Gitea 仍是主要 repo，ADO 只當 CI 鏡像。

本 skill 的內容來自六次實際導入：第一次從零安裝 Server，第二次接在已經裝好的同一台 Server 上（Gradle 8.14.3、主 repo＋依賴 repo），第三次也接在同一台 Server 上（Gradle 7.5.1、主 repo＋依賴 repo＋另一個獨立 repo），並做了從 Windows agent 經 SSH 部署到 Linux VM 的 CD（第 9 章）；第四次在一台全新的 Windows Server 2022 上安裝 Azure DevOps Server 25H2，並把既有專案的 CI／CD 搬過去；第五次把一個 Gradle 專案搬到另一台以 http 提供的既有 Server、新增 agent，並跑通 CD 的 dry-run；第六次在同一台 http 的 Server 上一次接上 5 個 repo（Gradle 主 repo＋依賴 repo、引用同層 repo 前端原始碼的 Gradle repo、Maven 新舊兩條線並存的 repo、Maven＋JDK 8 舊專案）。踩過的坑整理在 `references/pitfalls.md`，每條都有驗證狀態。

---

## 使用守則

1. **專案值不寫進本 plugin**：主機名、網址、專案代號、權杖、帳密都放在使用者自己的兩層設定檔（見「設定檔」）。專案層 `.ci-tools.yml` 要加進該專案的 `.gitignore`，或確認只放在私有 repo；使用者層 `servers.yml` 只放在自己的電腦。
2. **先探索、再動手**：每次都從「第 0 步：探索」開始，不要假設 server 不存在。
3. **不強制推送**：同步只做快轉，分叉就停下來交給使用者決定。
4. **保護分支由使用者自己推**：遇到 `main`、`master`、`production`，只印出指令，不代為執行（踩坑 24）。
5. **保留驗證狀態**：範本與踩坑表標示「未驗證」「估計」「未釐清」的項目，轉述給使用者時照原標示說明，不要改寫成肯定句。
6. **驗證看 API，不看截圖**：建置是否成功，以第 5 章的 REST API 結果為準。
7. **給使用者在 Windows 主控台執行的指令只用 ASCII**：中文（含計算欄位名稱）貼進 Windows PowerShell 5.1 互動視窗會變亂碼並吃掉引號（踩坑 53）。
8. **權杖能不用就不用**：agent 設定可用 Integrated 驗證時不要請使用者產生權杖（踩坑 52）；使用者在對話中貼出權杖原文時，立即提醒撤銷。

## 範本驗證狀態

| 範本 | 狀態 |
|------|------|
| `pipeline-templates/gradle17.yml` | 結構（`jobs`＋`timeoutInMinutes`、指定 Gradle task、依賴 repo 並排 checkout、離線建置、近期測試篩選、init script）已在第二個真實專案（Gradle 8.14.3、主 repo＋1 個依賴 repo）跑到綠燈；第三個專案（Gradle 7.5.1）以**寫死的同類 yaml**（非本範本）驗證了依賴 repo 並排 checkout、離線建置、「沒有近期測試就只建置」（連線與離線各一次）與 Build Artifacts 發佈，但走的是只建置模式，**沒有執行到近期測試篩選與 init script**。本檔是去識別化並參數化的改寫版，**改寫後未在 ADO 重新執行**。其中 `dependencyRepos` 以 `${{ each }}` 產生 checkout 的寫法**未驗證**（真實專案是寫死的） |
| `cd-templates/probe-agent-ssh.yml` | 已在實際專案跑過（確認 agent 能 TCP 連到 VM 並完成 SSH 握手、列出 agent 上可用的工具） |
| `cd-templates/probe-agent-ssh-key.yml` | 同結構已在第五次導入跑通（以部署金鑰登入、釘選主機金鑰）；本檔為去識別化版，改寫後未重跑 |
| `cd-templates/deploy-vm.ps1`、`cd-stage-snippet.yml`、`push-deploy-branch.sh` | **未驗證**：來源專案的 CD 實作已跑通 dry-run 與正式部署，但這三個檔是去識別化改寫版，改寫後未執行過；`push-deploy-branch.sh` 的 `--apply` 連來源版本都沒用過（見 `cd-ssh-vm.md`）。`deploy-vm.ps1` 的預檢經 stdin、dry-run 不寫 VM，與 `cd-stage-snippet.yml` 的 downloadPath 子目錄，都取自第五次導入已跑通 dry-run 的做法 |
| `pipeline-templates/maven8.yml` | 同結構（`%M2_HOME%` 呼叫 mvn、`demands: maven`、預設連線）已在第六次導入以**寫死的 yaml** 跑通：JDK 8 只編譯打包兩個專案、同寫法的 JDK 17 版含 clean verify 與發佈測試結果。本檔是參數化改寫版，**改寫後未在 ADO 重新執行** |
| `pipeline-templates/maven25.yml` | **未驗證**（JDK 25 未跑過；mvn 呼叫方式等已依第六次導入的 JDK 8／17 實測修正） |
| `pipeline-templates/vue.yml` | **未驗證** |
| `pipeline-templates/python.yml` | **未驗證** |
| `ci-init.gradle` | 已在最小專案（Gradle 8.14.3）實測：覆寫 `failFast`、`forkEvery`、空清單失敗、只跑清單內測試、排除清單、清單帶 BOM |

未驗證的範本已改成 `jobs:` 結構並加上 `timeoutInMinutes`，但仍未在任何 ADO 上跑過。套用未驗證的範本時，先告訴使用者它沒有跑過，第一次建置後再依第 5 章確認。

## 設定檔（兩層）

server 層的資訊（主機、collection、SSH 金鑰、權杖變數名、agent 上的 JDK／Gradle 快取／npm 快取路徑、已快取的 Gradle 版本）每個專案都一樣，只寫一次；專案層只寫這個專案自己的事。

| 層 | 檔案 | 位置 | 範例 |
|----|------|------|------|
| 使用者層 | `servers.yml` | `${XDG_CONFIG_HOME:-~/.config}/ci-tools/servers.yml`（腳本也接受 `--servers` 或環境變數 `CI_TOOLS_SERVERS`） | `references/servers.example.yml` |
| 專案層 | `.ci-tools.yml` | 專案 repo 根目錄 | `references/ci-tools.example.yml` |

- `servers.yml` 可以有多個 server；`.ci-tools.yml` 用 `server: <名稱>` 引用（省略時用 `default_server`）。
- `.ci-tools.yml` 的 `repos` 是清單：一個專案可能有主 repo 加依賴 repo（`role: main|dependency`）。各 repo 的 ADO SSH 網址由 server 的 `ssh_url` 樣式代入 `{collection}` `{project}` `{repo}` 推出，需要時可在 repo 上直接寫 `ado_url` 覆寫。
- 腳本用 `scripts/lib/ci_config.py` 讀這兩個檔：只用 python3 標準函式庫，**不需要 PyYAML 或 yq**。代價是只支援 YAML 子集（巢狀 mapping、清單、`[a, b]`、引號、註解），超出就報錯，不會猜。
- 舊版單層 `.ci-tools.yml`（`gitea_url`、`ado_url` 在最外層）仍可被 `ci-discover.sh` 讀出 git 主機，但同步與分類腳本需要新格式，請照範例改寫。

## 權杖（PAT）與權限範圍

權杖的**值**不寫進任何檔案。`servers.yml` 的 `pat_env` 只記「權杖放在哪個環境變數」（預設 `ADO_PAT`），腳本從那個變數讀、不會印出。使用者可能同時有一個唯讀權杖和一個可寫權杖，`pat_env` 指向要用的那一個。

| 動作 | 需要的範圍 |
|------|-----------|
| 查詢專案、repo、建置、timeline、log | 讀取範圍即可（唯讀權杖只能做這一列） |
| 建立 ADO 專案 | Project and Team：讀取、寫入與管理（第五次導入實測） |
| 建 repo、改名、改預設分支、透過 HTTPS 推送 | Code：讀取與寫入（SSH 推送用金鑰，不用權杖；推 repo 與改預設分支為第五次導入實測） |
| 建 pipeline 定義、排入建置 | Build：讀取與執行（第五次導入實測） |
| 上傳 secure file（CD 的部署金鑰） | Secure Files：讀取、建立及管理（已遇到兩次：只有讀取時清單查得到、上傳回 401） |
| 授權 pipeline 使用資源（`pipelinePermissions`，踩坑 35） | **未驗證**是哪個範圍（實測時權杖已含 Build 讀取與執行等多個範圍） |
| `ado-bootstrap-project.sh --apply`（建專案、建／改名 repo、改預設分支、建 pipeline、授權） | 上面幾列的合計：Project and Team 讀取、寫入與管理；Code 讀取與寫入；Build 讀取與執行；授權那一步的範圍同上一列**未驗證**。整支腳本沒有在真實 server 上以最小範圍權杖跑過，**哪些範圍足夠未驗證** |
| `ado-bootstrap-project.sh --import`（建立與刪除暫時的 service endpoint、匯入要求） | 另加 Service Connections：讀取、查詢與管理；Code 需含管理（官方文件標的是 `vso.code_manage`）。**未驗證** |
| 讀寫 Wiki 頁面 | **未驗證**是哪個範圍（依官方名稱應為 Wiki 讀取與寫入）；改既有頁面要帶 `If-Match`（踩坑 43） |

**401 的判讀**（踩坑 32、5、34）：

- **所有請求都 401**（連查詢都不行）→ 先查 IIS 基本驗證是否停用（踩坑 5）。
- **查詢正常、只有寫入 401** → 權杖範圍不足（踩坑 32）。換用有對應範圍的權杖（改 `pat_env`），或改走網頁：
  - 建專案：首頁 **New project**
  - 建 repo：專案的 **Repos → New repository**；改預設分支：**Repos → Branches**，在正式分支的選單選 **Set as default branch**
  - 建 pipeline：**Pipelines → New pipeline → Azure Repos Git → Existing Azure Pipelines YAML file**，選分支與 yaml 路徑
  - 執行：**Run pipeline** 時選含 yaml 的分支（踩坑 28）
- **建立 pipeline 回 401 不代表沒建成**（踩坑 34）：先列出 pipeline 確認，不要重送，否則會建出重複的定義。
- 權杖的實際範圍也可能比以為的大（踩坑 23，**範圍細節未釐清**），發出前先確認。

範例（bash；權杖以 curl 設定檔從 stdin 傳入，不會出現在指令列或 `ps`）：

```bash
PAT_ENV=ADO_PAT                                  # servers.yml 的 pat_env
ADO=http://ado-host:8080/Collection              # servers.yml 的 url/collection
ado() { printf 'user = ":%s"\n' "${!PAT_ENV}" | curl -sS -K - "$@"; }   # zsh 改用 ${(P)PAT_ENV}
ado "$ADO/_apis/projects?api-version=6.0"
```

---

## 第 0 步：探索（每次都先做）

ADO server 常常早就裝好了，資訊散在其他專案的 `azure-pipelines.yml`、`~/.ssh` 的金鑰、shell 的環境變數裡。先跑探索腳本，不要假設要從零開始：

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/ci-discover.sh" [--search-root <放各專案的上層目錄>] [--no-network]
```

它只讀不寫，網路只發 GET，會列出：

1. `servers.yml` 裡的 server、`pat_env` 變數是否已設定（不顯示值）、金鑰檔是否存在、agent 路徑與已快取的 Gradle 版本
2. 本專案的 `.ci-tools.yml`、`azure-pipelines.yml`、Gradle wrapper 版本
3. 同層目錄與 `--search-root` 下（深度 3）其他專案的 `azure-pipelines.yml`、`.ci-tools.yml`，以及裡面可以直接搬進 `servers.yml` 的 `JAVA_HOME`、`GRADLE_USER_HOME`、`NPM_CONFIG_CACHE`、引用的 ADO repo
4. `~/.ssh` 下的金鑰：只用 `.pub` 判斷型別與長度，**不讀私鑰內容**；標出 RSA（ADO 內建 SSH 只收 RSA，踩坑 4）
5. git 平台：用 `/api/v1/version`（Gitea）、`/api/v4/version`（GitLab）的回應判斷，**不看主機名**（踩坑 31）
6. 有 server 且權杖變數已設定時，列出 ADO 上的專案與 agent pools
7. **測試外部連線**（踩坑 62）：先剝除註解，再列出測試設定與測試原始碼裡的 `jdbc:`／`mongodb://`／`redis://` 等連線（只印主機與埠，不印帳密），主機與 servers.yml 的 server 同機標「⚠️ 與 ADO server 同機」、`localhost` 標「CI 上會連到 agent 本機」；另列出會啟動 Spring context 且未 `@Ignore`／`@Disabled` 的測試類
8. **跨 repo 相對引用**（踩坑 63）：前端檔的相對 `import` 跳出 repo 根目錄者，列出檔案與解析後的目標

最後給建議，依結果分三條路：

| 探索結果 | 下一步 |
|----------|--------|
| `servers.yml` 已有 server | 跳過「0. 評估」與第 1、2 章，直接到第 3 章 |
| 沒有 `servers.yml`，但找到其他專案的 CI 設定 | server 很可能已存在：從找到的檔案整理出 `servers.yml`（照範例），向使用者確認後再到第 3 章 |
| 什麼都沒找到 | 只有 RSA 金鑰時先問使用者是否已有 server；確定沒有才從「0. 評估」開始 |

找到同一台 server 上其他專案的 pipeline 時，JDK 路徑、快取目錄、多 repo 寫法都可以直接參考。

## 0. 評估（只在探索結果沒有可用 server 時）

動手前先確認下列項目，有任何一項不符就先跟使用者討論：

- **OS 版本**：對照官方支援清單。實測時用了不在清單內的 Windows 11 版本，安裝檢查能過，但屬不受支援組合（踩坑 21）。
- **SQL Server 版本與版次**：Express 可用。Developer 版授權只限開發測試，不能當日常服務（踩坑 21）。既有的舊版 SQL 若版本不符或缺全文檢索，就另裝新的執行個體。
- **授權與使用人數**：ADO Server Express 限 5 位活躍使用者。超過就要評估正式授權。
- **自架 agent 的並行數**：Server 版自架 agent 不收並行費用，並行數等於 agent 數。
- **程式碼能不能上雲**：如果可以，雲端版 Azure DevOps Services 免自架，先跟使用者確認這個選項是否已排除。

## 1. 安裝 Server

1. 選 **Express** 版本。
2. **SQL Server 先手動安裝**，服務帳戶用虛擬帳戶 `NT Service\MSSQL$<執行個體名稱>`，不要讓 ADO 精靈用 NetworkService 代裝（踩坑 1）。安裝卡在 RebootRequiredCheck 時加 `/SKIPRULES=RebootRequiredCheck`（踩坑 2）。
3. ADO 精靈選「使用現有執行個體」。部署類型選「**新的部署 - 進階**」，不要選「現有的部署」（那是資料庫已存在時用的）；SQL 執行個體欄位寫 `.\<執行個體>` 或 `localhost\<執行個體>`，只寫執行個體名稱會被當成電腦名稱（踩坑 47）。
4. **防火牆**：確認網卡的網路類型。若是「公用」，精靈建的規則不會生效，改成「私人」或另建限定來源網段（例：`192.0.2.0/24`）的規則（踩坑 3）。
5. **連接埠**：記下精靈設定的連接埠，寫進 `servers.yml` 的 `url`。
6. **IIS 基本驗證**：停用，否則所有 PAT 都會 401（踩坑 5）。
7. **公用 URL**：預設是主機的電腦名稱，主機在雲端或工作群組時用戶端常解析不到。安裝時或之後在管理主控台「變更公用 URL」改成用戶端連得到的 IP 或 FQDN（踩坑 49）。
8. **工作群組主機的帳號**：ADO 使用者就是主機上的本機 Windows 帳號，先在主機建帳號再到 ADO 加群組，登入與人員選取器都寫 `<主機名>\<帳號>`；非英文介面的內建群組名稱會被翻譯（踩坑 48）。
9. **套用 Patch**：先關閉管理主控台（`TfsMgmt.exe`），否則修補工具會停止；修補後確認 Git SSH 服務 `TeamFoundationSshService` 的啟動類型是自動，否則重開機後 SSH 會斷（踩坑 50）。

## 2. 安裝 agent

1. 網頁的「新增代理程式」對話框若沒有內容，改用 REST API `_apis/distributedtask/packages/agent?platform=win-x64` 取得版本與下載網址（踩坑 19）。
2. 同一台機器可以裝多個 agent，各用不同資料夾與名稱。**加之前先在建置進行中量記憶體**（總量／可用量與前十名程序，指令見踩坑 22）：實測一個 Gradle 建置（`-Xmx3g`＋測試 fork 3G）約 9～10GB，ADO 主機上還常駐 IIS、SQL Server 等；「每個建置 6～8GB」只是**粗估**。
3. 在 agent 加上使用者自訂的 capability 標籤，例如 `JDK17`、`JDK8`、`node`、`python`，讓 pipeline 用 `demands` 指定。
4. 安裝 JDK、Node、python 等工具時注意：
   - agent 以 NetworkService 執行時沒有使用者資料夾，npm 快取要指到可寫路徑（`NPM_CONFIG_CACHE`，踩坑 7）。
   - agent 服務的 PATH 沒有 git，腳本步驟要用 `<agent 目錄>\externals\git\cmd\git.exe`（踩坑 6）。
   - 工具要裝成全機共用（python 用 `InstallAllUsers=1 PrependPath=1`），裝在個人帳號下的 NETWORK SERVICE 讀不到；有沒有裝好以探測 pipeline 的 `Get-Command` 為準，能力清單沒列不代表沒有（踩坑 57）。
   - 裝完要重啟 agent 服務才會讀到新的 PATH；**重啟會取消正在跑的建置**，先確認 agent 閒置（踩坑 54）。集區可能被同一個 collection 的其他專案共用，判斷 agent 在跑誰的建置要看 jobrequest 的 `scopeId`，不要看名稱或建置編號（踩坑 55）。
5. 把 JDK 路徑、`GRADLE_USER_HOME`、npm 快取路徑記進 `servers.yml` 的 `agents`，之後每個專案共用。
6. **agent 與 ADO 同一台**（踩坑 51）：`config.cmd` 的驗證類型用預設的 Integrated（目前登入的 Windows 帳號），不需要 PAT；裝在別台才需要 PAT。**Server 以 http 提供時 `--auth pat` 會失敗**（要求安全連線），用 `--auth integrated`（已驗證的只有 agent 與 Server 同一台；「Server 用 http 且 agent 在別台」目前沒有驗證過的做法，見踩坑 52）；`--url` 照抄既有 agent 的 `.agent` 檔裡的 `serverUrl`，下載、解壓、`config.cmd` 的完整指令見踩坑 52。新 agent 第一次建置要連線跑（踩坑 38）。agent 會自動偵測已安裝的 JDK 並登記 `jdk_17_x64` 等能力（不看 `JAVA_HOME`），`demands` 可直接用。agent 以 NETWORK SERVICE 執行時，共用快取目錄要給它修改權限（`icacls <目錄> /grant "NT AUTHORITY\NETWORK SERVICE:(OI)(CI)M"`；此項為預防，**未驗證**）。

## 3. 建立 ADO 專案與匯入 repo（Gitea → ADO）

### 3.1 建立 ADO 專案與 repo

需要 Project and Team（讀取、寫入與管理）與 Code（讀取與寫入）範圍的權杖；範圍不足時改走網頁（見「權杖（PAT）與權限範圍」）。本節到 3.3 的 REST 步驟可以用 `ado-bootstrap-project.sh` 照順序一次做完（第 6A 章；步驟 1～4 已在真實 server 跑過，建 pipeline 與依 yaml 授權仍只有離線測試）；以下是手動做法，也是腳本每一步在做的事。以下流程在第二個專案實際做過（已遇到）；`api-version` 依伺服器版本指定，範例用 servers.yml 的 `api_version`。

1. **取得流程範本 id**：用同一台 server 上既有專案的設定，不要自己猜。

   ```bash
   ado "$ADO/_apis/projects/<既有專案>?includeCapabilities=true&api-version=6.0"
   # 取 capabilities.processTemplate.templateTypeId
   ```

2. **建專案**：`POST $ADO/_apis/projects?api-version=6.0`，body：

   ```json
   {"name": "project-a", "description": "CI 鏡像",
    "capabilities": {"versioncontrol": {"sourceControlType": "Git"},
                     "processTemplate": {"templateTypeId": "<上一步的 id>"}}}
   ```

   回 **202** 與一個 operation，建立是非同步的：輪詢 `GET $ADO/_apis/operations/<operation id>?api-version=6.0`，直到 `status` 為 `succeeded`（`failed`／`cancelled` 就停下來回報）。

3. **預設 repo 改名**：建專案會自帶一個與專案同名的 repo。名稱不符時先 `GET $ADO/<專案>/_apis/git/repositories?api-version=6.0` 取得它的 `id`，再 `PATCH $ADO/<專案>/_apis/git/repositories/<id>?api-version=6.0`，body `{"name": "<主 repo 名稱>"}`。
4. **依賴 repo**：主 repo 的 `settings.gradle` 引用同層的其他 repo（例：核心模組）時，另外建：`POST $ADO/<專案>/_apis/git/repositories?api-version=6.0`，body `{"name": "product-core", "project": {"id": "<專案 id>"}}`。
5. 每個 repo 都寫進 `.ci-tools.yml` 的 `repos`（主 repo `role: main`，其餘 `role: dependency`）。
6. **大型 repo 可用 ADO 的「匯入存放庫」**（踩坑 46）：由 ADO server 直接從 Git 平台拉，不經過開發者電腦或 VPN。注意入口：Repos 上方 repo 下拉選單的「匯入存放庫」一律**新建** repo（名稱預設取來源網址最後一段，含 `.git`）；要匯入既有的空 repo，得在那個 repo 的頁面上按「匯入」。匯入會帶入所有分支、預設分支沿用來源，之後照 3.2 第 4 步改預設分支。

### 3.2 推送與預設分支

1. 準備 CI 專用的 **RSA 4096** 金鑰並加到 ADO（ed25519 不被接受，踩坑 4），路徑寫進 `servers.yml` 的 `ssh_key`。測試連線時加 `-o IdentitiesOnly=yes -o IdentityAgent=none`。
2. 用 bare clone 從 Gitea 取得 repo，**只推分支**，不要用 mirror 推送。建議直接用第 6 章的同步腳本：新分支會被推上去，保護分支只印指令，最後自動用 `git ls-remote` 比對。多 repo 時**先推依賴 repo、再推主 repo**（`sync-all-repos.sh` 依 `role` 自動排序）。
3. 推完**逐分支比對 SHA**：兩邊各跑 `git ls-remote`，每個分支的 SHA 都要一致。
4. **立刻改預設分支**：ADO 會把第一個收到的分支當預設，常常變成測試分支（踩坑 18）。`PATCH $ADO/<專案>/_apis/git/repositories/<id>?api-version=6.0`，body `{"defaultBranch": "refs/heads/main"}`。路徑**一定要用 repo 的 id**，用名稱會回 400（踩坑 27）。
5. 觸發延遲：透過 SSH 推送後，曾觀察到 CI 觸發的建置約 30 分鐘後才出現（踩坑 29，**原因未釐清**）。急著驗證時直接手動執行，不要等——但手動排入前，先用 `statusFilter=inProgress,notStarted` 查有沒有已經觸發、還在排隊或執行中的建置，只看已完成清單會誤判成沒觸發而多跑一次（踩坑 56）。

### 3.3 建 pipeline 與授權資源

1. 用 REST 建 pipeline（`POST $ADO/<專案>/_apis/pipelines?api-version=6.0-preview.1`，`configuration` 指向 repo id 與 yaml 路徑）或走網頁。**回 401 先列出 pipeline 確認**，可能已經建好（踩坑 34）。**回 500「找不到檔案」**：這個 API 只到 repo 的預設分支找 yaml；yaml 只在別的分支時改用 `POST $ADO/<專案>/_apis/build/definitions?api-version=6.0`，在 `repository.defaultBranch` 指定含 yaml 的分支，不必改 repo 的預設分支（踩坑 58，body 見該條）。
2. **新專案一定要授權**，否則觸發後永遠 `notStarted`（踩坑 35）：agent 佇列、yaml 裡 `resources.repositories` 引用的依賴 repo、CD 用到的 secure file。
   ```bash
   "${CLAUDE_PLUGIN_ROOT}/scripts/ado-authorize.sh" list  --queue Default
   "${CLAUDE_PLUGIN_ROOT}/scripts/ado-authorize.sh" grant --queue Default      --pipelines <id>,<id>
   "${CLAUDE_PLUGIN_ROOT}/scripts/ado-authorize.sh" grant --repo product-core  --pipelines <id>
   ```
   寫入後腳本會讀回比對，不符就失敗。
3. 推含 yaml 的新 repo（例如臨時探測用）時，ADO 會自動建一條 `<repo> CI`（踩坑 36），清理時一併刪除。

## 4. 套 pipeline 範本

0. **先看第 0 步的「測試外部連線」**（踩坑 62）：有任何一項時，第一次 CI **只建置**（Maven `clean package -DskipTests`、Gradle `assemble`），確認測試不會連真實資料庫或外部服務後才開測試；只跑近期測試的專案，把會連真實環境的測試類加進 `ci-init.gradle` 的 `EXCLUDED_TESTS`，避免日後有人修改它就被選進 CI。「跨 repo 相對引用」有命中時，用 `resources.repositories` 並排 checkout 被引用的 repo，並在被引用處安裝它自己的 npm 相依（踩坑 63、64）。
1. **先找同一台 server 上「同建置工具版本、已綠燈」的既有專案 pipeline**（`ci-discover.sh` 會列出其他專案的 `azure-pipelines.yml`，再用第 5 章的 API 確認它最近的建置成功）。有的話以它為底改寫，比通用範本可靠；沒有才依專案類型選 `references/pipeline-templates/` 裡的範本，並告知使用者它的驗證狀態（見上方表格）。
2. 參數預設值從兩層設定填入：`.ci-tools.yml` 的 `gradle_tasks`、`recent_test_days`、`timeout_minutes`、`offline`，`servers.yml` agent 的 `jdk`、`gradle_user_home`、`npm_cache`。
3. Gradle 專案：把 `references/ci-init.gradle` 複製到專案的 `ci/ci-init.gradle`，pipeline 以 `--init-script` 載入，**不改專案的 build.gradle**。它會：
   - 在 `gradle.projectsEvaluated` 內設定 `failFast = false`（踩坑 10）、`forkEvery = 1`（踩坑 11）
   - 依 `RECENT_TESTS_FILE` 只跑近期修改的測試，清單為空就讓建置失敗（踩坑 12）。測試多年沒動的專案，範本改用 `emptyRecentTests: buildOnly`：沒有近期測試時明確切換成只建置、不載入 init script
   - 套用 `EXCLUDED_TESTS` 排除清單，每筆都要寫原因與移除條件（第 5.1 節）
4. 參數重點：
   - `timeoutInMinutes`：預設 120。ADO 預設 job 逾時 60 分鐘，首次建置（下載依賴＋獨立 JVM 測試）不夠（踩坑 26）。範本都是 `jobs:` 結構，逾時設在 job 上。
   - `gradleTasks`：預設 `test`。多模組專案若有子專案本來就編不過，明確指定 `:test :product-core:test`（踩坑 30）；只想跳過某個子專案的測試則用 `extraGradleArgs` 傳 `-x <子專案>:test`（踩坑 13）。兩者都要列為待辦。
   - `offline`：離線開關（預設開）。動態版本依賴在離線時會解析不到（踩坑 15）。
   - `dependencyRepos`＋`resources.repositories`：`settings.gradle` 引用同層的其他 repo 時，兩處都要填，並排 checkout，並先確認被引用 repo 的分支含有需要的 commit（踩坑 14）。
5. **第一次建置前的檢查**：
   - 比對專案 `gradle/wrapper/gradle-wrapper.properties` 的版本與 `servers.yml` 的 `cached_gradle_versions`（`ci-discover.sh` 會自動比對）。不在清單內就**第一次取消 offline 連線跑**，成功後把版本補進清單（踩坑 25）。
   - **版本在清單內也不保證離線能過**：這個專案自己的依賴可能沒被這台 agent 下載過（踩坑 38）。新專案第一次建置一律連線跑（`ado-run.sh ... -p offline=false`），成功後再用預設的離線模式跑一次確認。
   - pipeline 檔目前只在測試分支時，網頁第一次 Run 要把分支改成含 yaml 的分支，否則會報找不到有效 YAML（踩坑 28）。
6. 明確傳 `-Dorg.gradle.jvmargs=-Xmx3g -XX:MaxMetaspaceSize=1g`，不依賴開發者的個人設定（踩坑 8）。
7. 測試需要的環境變數放在 pipeline 的 `env`，值用 pipeline 變數或 secret（踩坑 9）。

## 5. 以 REST API 驗證建置

不要只看網頁截圖。用權杖呼叫 REST API 取證（用法見「權杖（PAT）與權限範圍」的 `ado` 函式）。以下路徑的 `$ADO` 是 `<url>/<collection>`：

| 要確認的事 | API |
|------------|-----|
| 最近一次建置的結果 | `GET $ADO/<專案>/_apis/build/builds?definitions=<定義 id>&$top=1` |
| 依建置編號找建置 | `GET $ADO/<專案>/_apis/build/builds?buildNumber=<編號>` |
| 哪個步驟失敗 | `GET .../_apis/build/builds/<buildId>/timeline`，找 `result` 為 `failed` 的 record |
| 失敗步驟的 log | `GET .../_apis/build/builds/<buildId>/logs/<logId>`（logId 取自 timeline record） |
| 測試統計 | `GET $ADO/<專案>/_apis/test/runs?buildUri=<build 的 uri>`，看 `totalTests`、`passedTests` 等欄位 |

`api-version` 依伺服器版本指定。

觸發與等待結果可以用 `ado-run.sh`（排入建置只需要 Build：讀取與執行）：

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/ado-run.sh" --config .ci-tools.yml --pipeline "<pipeline 名稱或 id>" --branch main -p offline=false
# 輪詢到完成；失敗時印出驗證訊息（validationResults）、失敗步驟名稱與 log 末段。--no-wait 只觸發不等待

# yaml 以 resources.repositories 引用其他 repo 時，可指定被引用 repo 的分支（別名＝yaml 的 repository 欄位；可重複）
"${CLAUDE_PLUGIN_ROOT}/scripts/ado-run.sh" --config .ci-tools.yml --pipeline <id> --branch <分支> --resource-ref <別名>=UAT

# 推送自動觸發的建置：不另外觸發，只等它跑完並印出結果（buildId 照第 56 條查排隊中／執行中取得）
"${CLAUDE_PLUGIN_ROOT}/scripts/ado-run.sh" --config .ci-tools.yml --wait-build <buildId>
```

判讀時注意：

- 建置幾秒內就 failed、timeline 回 HTTP 204（沒有任何 record）：pipeline 在派工前的驗證階段就失敗，原因在 `GET .../_apis/build/builds/<buildId>` 回應的 `validationResults`（`ado-run.sh` 會自動印出）。常見原因是 YAML 引用的 secure file 不存在或未授權；即使這次不會執行用到它的 stage 也一樣（踩坑 45）。
- 結果是 `canceled` 而且時間接近 60 分鐘，多半是 job 逾時（踩坑 26），不是測試失敗。
- 測試分頁的數字可能和 Gradle 輸出不同（踩坑 20，**原因未釐清**）。建置成敗以 Gradle 為準，數字差異如實回報，不要自行解釋原因。
- 開了近期測試篩選時，確認實際執行的測試數大於 0。`ci-init.gradle` 只擋得住「清單為空」，擋不住「清單有內容但全部對不上」。
- 401 依「權杖（PAT）與權限範圍」的判讀分辨是 IIS 基本驗證還是範圍不足。
- 依建置編號或 pipeline 名稱找建置時，確認專案是對的：同一個 collection 的其他專案可能有完全相同的編號與名稱（踩坑 55）。

### 5.1 首次建置失敗分類

第一次跑完整測試，常會冒出大量失敗，大多是產品原始版本本來就有的問題或環境差異，不是本專案改壞的（踩坑 33）。不要為了變綠去改測試，照這個流程分類：

1. **抽出失敗類別**（對 ADO 只發 GET）：

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/scripts/ci-triage.sh" --build <buildId 或建置編號> --config .ci-tools.yml
   # 已手動下載 log 時：--log-file <log>（可重複），不連網路
   ```

   它會抓 timeline 找失敗步驟、下載 log、萃取 `<類別> > <方法> FAILED`，統計每個類別的失敗方法數。log 只有簡短類別名時，用 `--repo-dir`（預設目前目錄）裡的原始檔補成完整類別名；補不出來的列在 `unresolved.txt`。產出 `failed-classes.txt`、`failed-tests.tsv`、`commands.txt`。
2. **本機重跑**：`commands.txt` 第 1 段用 `RECENT_TESTS_FILE=failed-classes.txt` 加上同一個 `--init-script` 只跑這些類別，條件與 CI 相同。
3. **與產品基準比對**：`commands.txt` 第 2 段用 `.ci-tools.yml` 的 `baseline_branch` 比對這些測試檔，沒有輸出＝本專案沒改過。
4. **判讀**：

   | 基準比對 | 本機結果 | 結論 | 處理 |
   |----------|----------|------|------|
   | 有差異 | — | 本專案改動造成 | 照一般 bug 修 |
   | 沒差異 | 也失敗 | 產品既有問題 | 排除＋另開單 |
   | 沒差異 | 通過 | 環境差異（Windows 路徑分隔符、被 gitignore 的設定檔不在 CI、需要容器或外部服務、外部網域已不存在） | 排除＋另開單；能補環境就補 |

5. **寫進排除清單**：`ci/ci-init.gradle` 的 `EXCLUDED_TESTS`，每筆附原因與移除條件，按原因分組，並註明是哪一次建置發現、追蹤單號：

   ```groovy
   // 首次完整建置（#<建置編號>）失敗、測試檔與產品基準相同的類別；追蹤單：<單號>
   // 移除條件：該測試在本機與 CI 皆通過後，刪除對應那一行
   def EXCLUDED_TESTS = [
       // 需要容器，CI agent 沒有
       ['com.example.sandbox.ContainerE2ETest', '需要 sandbox 容器', '容器可用或測試改為無容器時跳過'],
       // Windows 路徑分隔符為反斜線，斷言寫死 /tmp
       ['com.example.sandbox.PathTest', '斷言寫死 Unix 路徑；本機 macOS 通過', '斷言改用 File.separator'],
   ]
   ```

6. 排除後重跑一次，確認建置綠燈且實際執行的測試數符合預期（第 5 章的測試統計）。

## 6. 同步策略

- **Gitea 是主要 repo，ADO 是 CI 鏡像，只做單向快轉。**
- 單一 repo 用 `sync-gitea-to-ado.sh`：

  ```bash
  # 預覽（預設，不推任何東西）
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh" \
    --gitea <gitea_url> --ado <ado_url> --key <ssh_key> [--branches main,uat]

  # 確認預覽沒問題後才套用
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh" ... --apply
  ```

- 專案有多個 repo 時用 `sync-all-repos.sh`，參數全部從兩層設定檔組出來：

  ```bash
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-all-repos.sh" --config .ci-tools.yml            # 預覽
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-all-repos.sh" --config .ci-tools.yml --apply    # 套用
  ```

  它逐 repo 呼叫 `sync-gitea-to-ado.sh`，工作 clone 預設放在 `mirrors/<server>/<ado_project>/<repo>.git`（`--workdir-root` 可改），避免不同專案的同名 repo 共用同一份 clone（踩坑 67）。`--apply` 時先把所有 repo 預覽一遍，**任一 repo 有分叉或錯誤，所有 repo 都不推**（結束碼 2 或 1）；推送順序 `dependency` 先、`main` 後。`--only <name>` 只處理指定的 repo。
- 預覽會列出每個分支的狀態：`一致`、`可快轉 (領先 N)`、`ADO 領先 (N)`、`已分叉`、`新分支`、`僅 ADO`、`兩邊皆無`。
- `--apply` 的規則：
  - 只推 `可快轉` 與 `新分支`，不用強制推送。
  - 只要有一個選定分支是 `已分叉`，**整批都不推**，結束碼 2。請使用者先在 Gitea 端處理，或用 `--branches`（多 repo 時改 `.ci-tools.yml` 的 `branches`）排除該分支。
  - 保護分支只印出指令，請使用者自己執行（踩坑 24）。多 repo 時 `sync-all-repos.sh` 在最後把所有 repo 的待推指令彙整成一段，並寫成 `<工作 clone 根目錄>/pending-protected-pushes.sh`（不會自動執行；沒有待推時不產生）。
  - 工作 clone 已存在、但其 `gitea`／`ado` 遠端與本次不同時，單 repo 腳本直接停止、不覆蓋遠端（踩坑 67），請改用 `--workdir` 指定別的路徑。
  - 推完自動用 `git ls-remote` 比對兩邊 SHA。
- `ADO 領先` 表示有人直接推到了 ADO，違反單向原則。回報給使用者，不要自動處理。
- 工作 clone 預設放在 `${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/<server>/<ado_project>/`（用 `sync-all-repos.sh` 時；單 repo 腳本未指定 `--workdir` 時仍是 `mirrors/<repo>.git`），不放 `/tmp`（踩坑 16）。

## 6A. 接上新 server／換 server

專案已經在某台 ADO 上跑 CI，要接到另一台（或新專案第一次接上已裝好的 server）時，第 3 章的建專案、建 repo、改預設分支、建 pipeline、授權，可以用 `ado-bootstrap-project.sh` 照順序一次做完。手動做時最容易錯的幾步它都照踩坑處理：預設 repo 改名與改預設分支都用 repo id（踩坑 27）、建 pipeline 回 401 先重新列出（踩坑 34）、已有指向同一個 yaml 的 pipeline 就沿用（踩坑 36）、建完列出名稱以 `.git` 結尾或重複的 repo（踩坑 46）。

**驗證狀態**：第六次導入在真實 server 上以 `--apply` 跑過步驟 1～4（建專案並輪詢、預設 repo 改名、新建 repo、改預設分支，皆讀回一致）與步驟 7；**步驟 5 建 pipeline（含 build definitions 改道與補 triggers）、步驟 6 依 yaml 授權被引用 repo 仍只有離線測試**，`--import` 那條路徑連手動都沒用 REST 做過。用到未實跑的步驟時先預覽，`--apply` 後到網頁確認結果，並照實告訴使用者。

### 步驟

1. **servers.yml 加 server**：照 `references/servers.example.yml` 新增一段（`url`、`collection`、`ssh_url`、`ssh_key`、`pat_env`、`agents`）；整批搬家時可改 `default_server`。
2. **`.ci-tools.yml` 寫 `server: <名稱>`**，必要時加可選欄位 `ado_process`（流程範本名稱，省略時用 server 的預設範本）、`agent_queue`（省略時 `Default`）。
3. **預覽**（只發 GET，逐步印出「現況 → 將要做什麼」）：

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/scripts/ado-bootstrap-project.sh" --config .ci-tools.yml
   ```

4. **套用**：同一行加 `--apply`。每個寫入都會讀回比對，不一致就以結束碼 2 停下；任一步失敗即停。建 pipeline 時 pipelines API 回 500 且回應含找不到檔案類訊息，會自動改用 build definitions 並指定 `branches` 的第一個分支（踩坑 58），body 一律帶 CI `triggers` 並讀回確認——不帶的話推送不會自動建置（踩坑 60，已驗證）。沿用既有 pipeline 時，`triggers` 為空會在 `--apply` 補上；同一個 yaml 有兩條以上定義（ADO 推 yaml 時會自動建一條，踩坑 36）只警告、不刪除。步驟 6 另外讀本機 yaml 的 `resources.repositories`，把同專案被引用的 repo 一併授權。repo 還沒有內容時，預設分支與 pipeline 兩步會跳過，推送程式碼後**再跑一次**同一行就會補上（已存在的東西一律沿用，可以重複執行）。
5. **推送程式碼**：預設不動程式碼，用 `sync-all-repos.sh --config .ci-tools.yml --apply`（第 6 章）。大型 repo 可改用 `--import`，由 ADO server 直接從 Git 平台拉（踩坑 46），需要環境變數 `GITEA_USER`、`GITEA_TOKEN`，來源要是 http(s) 網址（repo 的 `import_url`，省略時用 `gitea_url`）。腳本會建一個暫時的 Git 類 service endpoint，匯入結束（成功或失敗）就刪掉。**此路徑未驗證**：endpoint 型別名稱 `git` 不在官方 REST 文件的範例裡，腳本會先向 server 查詢這個型別是否存在。
6. **上傳 secure file**（有 CD 時）：腳本不處理，只在結尾列出 yaml 引用的 secure file 名稱。第一次建置前先上傳到 **Library → Secure files**，再 `ado-authorize.sh grant --securefile <名稱> --pipelines <id>`，否則整條 pipeline 在驗證階段就失敗（踩坑 45）。
7. **改 yaml 中與 agent 不一致的路徑**：腳本第 7 步讀本機的 `azure-pipelines.yml`（`.ci-tools.yml` 所在目錄的上一層＋repo 名稱），把 `JAVA_HOME`、`GRADLE_USER_HOME`、`NPM_CONFIG_CACHE` 和 servers.yml 該 server 的 agents 比對，不一致只警告、不改檔。
8. **第一次建置連線跑**：`ado-run.sh --pipeline <id> -p offline=false`（踩坑 25、38），成功後再用預設的離線模式跑一次。
9. **把新的 Gradle 版本加進** servers.yml 該 agent 的 `cached_gradle_versions`。

### 換 server 的檢查清單

- [ ] yaml 的 `JAVA_HOME` 與快取路徑（`GRADLE_USER_HOME`、`NPM_CONFIG_CACHE`）是新 agent 上的路徑（腳本第 7 步會比對）
- [ ] 腳本與 yaml 裡寫死的舊 ADO 網址：腳本第 7 步會掃 `azure-pipelines.yml` 與 `bin/*.sh`，列出含 servers.yml 其他 server 主機名的位置；其他目錄要自己再找一次
- [ ] secure file 已在新 server 上傳並授權（踩坑 45）
- [ ] 會觸發部署的分支：在新 server 建立或移動前，先撤銷 pipeline 對部署金鑰的授權，取消自動觸發的那次後再恢復（踩坑 39）
- [ ] Wiki **不會**跟著搬：舊 server 的 Wiki 頁面要另外複製（改既有頁面要帶 `If-Match`，踩坑 43）
- [ ] 舊 server 上的 pipeline 停用或刪除，避免兩邊同時建置、同時部署

## 7. 測試分層與 E2E

詳見 `references/e2e-layering.md`。摘要：

- 單元測試：每次 push
- 冒煙測試：部署到測試環境後
- 回歸測試：每晚排程
- 完整測試：手動觸發（發版前）
- E2E 用獨立的 agent pool，同一個測試環境一次只跑一個（`lockBehavior: sequential`）

## 8. 對外存取（本期不實作）

讓外部同事存取自架 ADO 的方案（例如 Cloudflare Tunnel）本期不提供做法，只列已知限制：

- **PAT 與 IIS 基本驗證互斥**（已驗證）：Tunnel 若需要基本驗證，就會和 PAT 衝突（踩坑 5），方案要重新設計。
- **Git over HTTPS 有單一請求大小上限**：大型推送可能失敗。
- **同事端是否需要安裝 client**：依選用的方案而定，要先確認。
- **NTLM 經過反向代理可能失效**：**未驗證**。

使用者要求設定對外存取時，說明這是另一個議題，先列出上述限制，不要直接動手。

## 9. CD：從 agent 經 SSH 部署到 VM

完整做法見 `references/cd-ssh-vm.md`，範本在 `references/cd-templates/`。**來源專案的整體流程已跑通 dry-run 與正式部署；`deploy-vm.ps1` 等範本是改寫版、未驗證**，套用時照實告知使用者。摘要：

1. **先探測再設計**：用 `cd-templates/probe-agent-ssh.yml` 建臨時 pipeline，確認 agent 能 TCP 連到 VM、完成 SSH 握手，並列出 agent 上的工具。自架 Windows agent 的 MinGit 沒有 bash（踩坑 40），部署邏輯要用 PowerShell 5.1＋Windows 內建 OpenSSH。探測完刪掉臨時 repo 與自動建立的 pipeline（踩坑 36）。
2. **部署分支只建在 ADO**：Gitea 只能經 VPN、不設 push mirror 時，觸發點只能在 ADO。要部署時把 commit 推到 ADO 的部署分支；`cd-templates/push-deploy-branch.sh` 會檢查本機沒有未推上 Gitea 的 commit，再依序快轉依賴 repo、主 repo，最後移動部署分支。
3. **金鑰**：CD 專用金鑰（不重用個人金鑰），公鑰照 `cd-ssh-vm.md` 第 2 步加進 VM（備份、防重複、限制前綴、比對舊行）；私鑰上傳為 secure file（權杖要 Secure Files 範圍）並授權 pipeline；VM 主機金鑰以檔案釘選（keyscan 輸出濾掉 `#` 行、與已信任的指紋比對），`StrictHostKeyChecking=yes`。之後用 `cd-templates/probe-agent-ssh-key.yml` 以這把金鑰實際登入一次。先查 VM 的 `sudo -n true`：免密碼就不必把 sudo 密碼放進 ADO。
4. **pipeline 兩個 stage**：Build 照舊，部署分支才發佈產物（**Build Artifacts**，Server 版不支援 Pipeline Artifacts，踩坑 37）；Deploy stage 只在部署分支執行，提供 dry-run 參數（建議預設開）。`DownloadBuildArtifacts` 的 `downloadPath` 用專用子目錄，否則 `cleanDestinationFolder` 會刪掉剛簽出的程式碼（踩坑 59）。
5. **建立／移動部署分支會立刻觸發真部署**（踩坑 39）：先用 `ado-authorize.sh revoke --securefile <名稱> --pipelines <id>` 撤銷授權 → 移動分支 → 取消自動觸發的建置 → 恢復授權 → 手動以 dry-run 執行。
6. **PowerShell 5.1**：`.ps1` 存 UTF-8 with BOM（踩坑 41）、開頭設 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`、包裝原生程式時暫改 `ErrorActionPreference`（踩坑 42）；推送前用 PowerShell 7 模擬 5.1 讀檔做解析檢查。
7. **遠端預檢零落地**：預檢腳本以位元組經 ssh stdin 送進 `bash -s`，dry-run 不在 VM 寫任何東西；事後用 `find <專案根目錄> -newermt <時間>` 確認（排除服務自身持續寫入的檔案）。專案已有遠端部署腳本時不改它，改用包裝腳本（`setsid nohup`＋`sudo -n flock -n -E 75`，log 與結果檔放在原腳本會刪除的目錄之外；舊版 util-linux 的 flock 沒有 `-E`，預檢要先檢查）。見 `cd-ssh-vm.md`。
8. **agent 上的 ssh／scp 不要設 `ConnectTimeout`**（踩坑 44）：Windows 版 OpenSSH 8.1 會每次等滿逾時才連上，分段上傳慢十倍以上而撞到 job 逾時。上傳慢時先用探測 pipeline 量 `ssh true` 的耗時，再對照 .NET 直接建 TCP 連線的時間。

---

## 參考檔

| 檔案 | 內容 |
|------|------|
| `references/pitfalls.md` | 67 條踩坑：症狀、根因、解法、驗證狀態 |
| `references/servers.example.yml` | 使用者層設定範例（server、權杖變數名、agent 路徑、已快取 Gradle 版本） |
| `references/ci-tools.example.yml` | 專案層設定範例（repos 清單、pipeline 參數、基準分支） |
| `references/ci-init.gradle` | Gradle CI init script |
| `references/pipeline-templates/` | 五個 pipeline 範本 |
| `references/e2e-layering.md` | 測試分層與 E2E 執行原則 |
| `references/cd-ssh-vm.md` | CD：從 Windows agent 經 SSH 部署到 VM 的流程、陷阱與遠端腳本契約 |
| `references/cd-templates/` | CD 範本：探測 pipeline（不帶金鑰／帶部署金鑰）、agent 端部署腳本、Deploy stage 片段、一鍵觸發腳本 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ci-discover.sh` | 第 0 步：唯讀探索既有設定並建議下一步 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh` | Gitea → ADO 單一 repo 單向快轉同步 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/sync-all-repos.sh` | 依 `.ci-tools.yml` 同步所有 repo（整批把關、依賴先推；工作 clone 依 server／專案分目錄；保護分支待推指令彙整成 `pending-protected-pushes.sh`） |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ci-triage.sh` | 首次建置失敗分類：抽出失敗類別、產生本機重跑與基準比對指令 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ado-run.sh` | 觸發 pipeline（分支、`templateParameters`、被引用 repo 的分支 `--resource-ref`）、輪詢至完成、印出失敗步驟 log；`--wait-build` 只等推送已觸發的建置 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ado-authorize.sh` | 列出／授權／撤銷 pipeline 對佇列、repo、secure file 的使用權限，寫入後讀回比對 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ado-bootstrap-project.sh` | 第 6A 章：依 `.ci-tools.yml` 備好專案、repo、預設分支、pipeline、授權，並比對 agent 路徑（預設只預覽；步驟 1～4 已在真實 server 跑過，建 pipeline、依 yaml 授權與 `--import` 未在真實 server 跑過） |
| `${CLAUDE_PLUGIN_ROOT}/scripts/lib/ci_config.py` | 兩層設定檔的讀取器（無外部依賴） |
