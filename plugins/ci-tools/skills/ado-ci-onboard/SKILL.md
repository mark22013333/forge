---
name: ado-ci-onboard
description: 引導把自架 Azure DevOps Server（ADO）接上既有 Gitea 專案的 CI：先探索既有設定、評估與安裝 Server 與 agent、建立 ADO 專案與 repo、套 pipeline 範本、以 REST API 驗證建置、首次建置失敗分類、Gitea → ADO 單向同步（含多 repo）、測試分層。當使用者提到「ado-ci-onboard」、「導入 CI」、「自架 Azure DevOps」、「ADO Server」、「Gitea 同步到 ADO」、「pipeline 範本」、「CI 踩坑」時觸發此 Skill。
---

# ADO CI Onboard — 自架 Azure DevOps Server 導入 CI

把既有 Gitea 專案接上自架 Azure DevOps Server（以下簡稱 ADO）的 CI。Gitea 仍是主要 repo，ADO 只當 CI 鏡像。

本 skill 的內容來自兩次實際導入：第一次從零安裝 Server，第二次接在已經裝好的同一台 Server 上（Gradle 8.14.3、主 repo＋依賴 repo）。踩過的坑整理在 `references/pitfalls.md`，每條都有驗證狀態。

---

## 使用守則

1. **專案值不寫進本 plugin**：主機名、網址、專案代號、權杖、帳密都放在使用者自己的兩層設定檔（見「設定檔」）。專案層 `.ci-tools.yml` 要加進該專案的 `.gitignore`，或確認只放在私有 repo；使用者層 `servers.yml` 只放在自己的電腦。
2. **先探索、再動手**：每次都從「第 0 步：探索」開始，不要假設 server 不存在。
3. **不強制推送**：同步只做快轉，分叉就停下來交給使用者決定。
4. **保護分支由使用者自己推**：遇到 `main`、`master`、`production`，只印出指令，不代為執行（踩坑 24）。
5. **保留驗證狀態**：範本與踩坑表標示「未驗證」「估計」「未釐清」的項目，轉述給使用者時照原標示說明，不要改寫成肯定句。
6. **驗證看 API，不看截圖**：建置是否成功，以第 5 章的 REST API 結果為準。

## 範本驗證狀態

| 範本 | 狀態 |
|------|------|
| `pipeline-templates/gradle17.yml` | 結構（`jobs`＋`timeoutInMinutes`、指定 Gradle task、依賴 repo 並排 checkout、離線建置、近期測試篩選、init script）已在一個真實專案（Gradle 8.14.3、主 repo＋1 個依賴 repo）跑到綠燈；本檔是去識別化並參數化的改寫版，**改寫後未在 ADO 重新執行**。其中 `dependencyRepos` 以 `${{ each }}` 產生 checkout 的寫法**未驗證**（真實專案是寫死的） |
| `pipeline-templates/maven8.yml` | **未驗證** |
| `pipeline-templates/maven25.yml` | **未驗證** |
| `pipeline-templates/vue.yml` | **未驗證** |
| `pipeline-templates/python.yml` | **未驗證** |
| `ci-init.gradle` | 已在最小專案（Gradle 8.14.3）實測：覆寫 `failFast`、`forkEvery`、空清單失敗、只跑清單內測試、排除清單、清單帶 BOM |

四個未驗證範本已改成 `jobs:` 結構並加上 `timeoutInMinutes`，但仍未在任何 ADO 上跑過。套用未驗證的範本時，先告訴使用者它沒有跑過，第一次建置後再依第 5 章確認。

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
| 建立 ADO 專案 | Project and Team：讀取、寫入與管理 |
| 建 repo、改名、改預設分支、透過 HTTPS 推送 | Code：讀取與寫入（SSH 推送用金鑰，不用權杖） |
| 建 pipeline 定義、排入建置 | Build：讀取與執行 |

**401 的判讀**（踩坑 32、5）：

- **所有請求都 401**（連查詢都不行）→ 先查 IIS 基本驗證是否停用（踩坑 5）。
- **查詢正常、只有寫入 401** → 權杖範圍不足（踩坑 32）。換用有對應範圍的權杖（改 `pat_env`），或改走網頁：
  - 建專案：首頁 **New project**
  - 建 repo：專案的 **Repos → New repository**；改預設分支：**Repos → Branches**，在正式分支的選單選 **Set as default branch**
  - 建 pipeline：**Pipelines → New pipeline → Azure Repos Git → Existing Azure Pipelines YAML file**，選分支與 yaml 路徑
  - 執行：**Run pipeline** 時選含 yaml 的分支（踩坑 28）
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
3. ADO 精靈選「使用現有執行個體」。
4. **防火牆**：確認網卡的網路類型。若是「公用」，精靈建的規則不會生效，改成「私人」或另建限定來源網段（例：`192.0.2.0/24`）的規則（踩坑 3）。
5. **連接埠**：記下精靈設定的連接埠，寫進 `servers.yml` 的 `url`。
6. **IIS 基本驗證**：停用，否則所有 PAT 都會 401（踩坑 5）。

## 2. 安裝 agent

1. 網頁的「新增代理程式」對話框若沒有內容，改用 REST API `_apis/distributedtask/packages/agent?platform=win-x64` 取得版本與下載網址（踩坑 19）。
2. 同一台機器可以裝多個 agent，各用不同資料夾與名稱。記憶體以每個建置 6～8GB **粗估**（未量測），先開 3～4 個再觀察（踩坑 22）。
3. 在 agent 加上使用者自訂的 capability 標籤，例如 `JDK17`、`JDK8`、`node`、`python`，讓 pipeline 用 `demands` 指定。
4. 安裝 JDK、Node 等工具時注意：
   - agent 以 NetworkService 執行時沒有使用者資料夾，npm 快取要指到可寫路徑（`NPM_CONFIG_CACHE`，踩坑 7）。
   - agent 服務的 PATH 沒有 git，腳本步驟要用 `<agent 目錄>\externals\git\cmd\git.exe`（踩坑 6）。
5. 把 JDK 路徑、`GRADLE_USER_HOME`、npm 快取路徑記進 `servers.yml` 的 `agents`，之後每個專案共用。

## 3. 建立 ADO 專案與匯入 repo（Gitea → ADO）

### 3.1 建立 ADO 專案與 repo

需要 Project and Team（讀取、寫入與管理）與 Code（讀取與寫入）範圍的權杖；範圍不足時改走網頁（見「權杖（PAT）與權限範圍」）。以下流程在第二個專案實際做過（已遇到）；`api-version` 依伺服器版本指定，範例用 servers.yml 的 `api_version`。

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

### 3.2 推送與預設分支

1. 準備 CI 專用的 **RSA 4096** 金鑰並加到 ADO（ed25519 不被接受，踩坑 4），路徑寫進 `servers.yml` 的 `ssh_key`。測試連線時加 `-o IdentitiesOnly=yes -o IdentityAgent=none`。
2. 用 bare clone 從 Gitea 取得 repo，**只推分支**，不要用 mirror 推送。建議直接用第 6 章的同步腳本：新分支會被推上去，保護分支只印指令，最後自動用 `git ls-remote` 比對。多 repo 時**先推依賴 repo、再推主 repo**（`sync-all-repos.sh` 依 `role` 自動排序）。
3. 推完**逐分支比對 SHA**：兩邊各跑 `git ls-remote`，每個分支的 SHA 都要一致。
4. **立刻改預設分支**：ADO 會把第一個收到的分支當預設，常常變成測試分支（踩坑 18）。`PATCH $ADO/<專案>/_apis/git/repositories/<id>?api-version=6.0`，body `{"defaultBranch": "refs/heads/main"}`。路徑**一定要用 repo 的 id**，用名稱會回 400（踩坑 27）。
5. 觸發延遲：透過 SSH 推送後，曾觀察到 CI 觸發的建置約 30 分鐘後才出現（踩坑 29，**原因未釐清**）。急著驗證時直接手動執行，不要等。

## 4. 套 pipeline 範本

1. 依專案類型選 `references/pipeline-templates/` 裡的範本，並告知使用者它的驗證狀態（見上方表格）。
2. 參數預設值從兩層設定填入：`.ci-tools.yml` 的 `gradle_tasks`、`recent_test_days`、`timeout_minutes`、`offline`，`servers.yml` agent 的 `jdk`、`gradle_user_home`、`npm_cache`。
3. Gradle 專案：把 `references/ci-init.gradle` 複製到專案的 `ci/ci-init.gradle`，pipeline 以 `--init-script` 載入，**不改專案的 build.gradle**。它會：
   - 在 `gradle.projectsEvaluated` 內設定 `failFast = false`（踩坑 10）、`forkEvery = 1`（踩坑 11）
   - 依 `RECENT_TESTS_FILE` 只跑近期修改的測試，清單為空就讓建置失敗（踩坑 12）
   - 套用 `EXCLUDED_TESTS` 排除清單，每筆都要寫原因與移除條件（第 5.1 節）
4. 參數重點：
   - `timeoutInMinutes`：預設 120。ADO 預設 job 逾時 60 分鐘，首次建置（下載依賴＋獨立 JVM 測試）不夠（踩坑 26）。範本都是 `jobs:` 結構，逾時設在 job 上。
   - `gradleTasks`：預設 `test`。多模組專案若有子專案本來就編不過，明確指定 `:test :product-core:test`（踩坑 30）；只想跳過某個子專案的測試則用 `extraGradleArgs` 傳 `-x <子專案>:test`（踩坑 13）。兩者都要列為待辦。
   - `offline`：離線開關（預設開）。動態版本依賴在離線時會解析不到（踩坑 15）。
   - `dependencyRepos`＋`resources.repositories`：`settings.gradle` 引用同層的其他 repo 時，兩處都要填，並排 checkout，並先確認被引用 repo 的分支含有需要的 commit（踩坑 14）。
5. **第一次建置前的檢查**：
   - 比對專案 `gradle/wrapper/gradle-wrapper.properties` 的版本與 `servers.yml` 的 `cached_gradle_versions`（`ci-discover.sh` 會自動比對）。不在清單內就**第一次取消 offline 連線跑**，成功後把版本補進清單（踩坑 25）。
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

判讀時注意：

- 結果是 `canceled` 而且時間接近 60 分鐘，多半是 job 逾時（踩坑 26），不是測試失敗。
- 測試分頁的數字可能和 Gradle 輸出不同（踩坑 20，**原因未釐清**）。建置成敗以 Gradle 為準，數字差異如實回報，不要自行解釋原因。
- 開了近期測試篩選時，確認實際執行的測試數大於 0。`ci-init.gradle` 只擋得住「清單為空」，擋不住「清單有內容但全部對不上」。
- 401 依「權杖（PAT）與權限範圍」的判讀分辨是 IIS 基本驗證還是範圍不足。

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

  它逐 repo 呼叫 `sync-gitea-to-ado.sh`（單 repo 腳本的行為一行都沒改，所以另寫包裝腳本而不加 `--config`）。`--apply` 時先把所有 repo 預覽一遍，**任一 repo 有分叉或錯誤，所有 repo 都不推**（結束碼 2 或 1）；推送順序 `dependency` 先、`main` 後。`--only <name>` 只處理指定的 repo。
- 預覽會列出每個分支的狀態：`一致`、`可快轉 (領先 N)`、`ADO 領先 (N)`、`已分叉`、`新分支`、`僅 ADO`、`兩邊皆無`。
- `--apply` 的規則：
  - 只推 `可快轉` 與 `新分支`，不用強制推送。
  - 只要有一個選定分支是 `已分叉`，**整批都不推**，結束碼 2。請使用者先在 Gitea 端處理，或用 `--branches`（多 repo 時改 `.ci-tools.yml` 的 `branches`）排除該分支。
  - 保護分支只印出指令，請使用者自己執行（踩坑 24）。
  - 推完自動用 `git ls-remote` 比對兩邊 SHA。
- `ADO 領先` 表示有人直接推到了 ADO，違反單向原則。回報給使用者，不要自動處理。
- 工作 clone 預設放在 `${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/`，不放 `/tmp`（踩坑 16）。

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

---

## 參考檔

| 檔案 | 內容 |
|------|------|
| `references/pitfalls.md` | 33 條踩坑：症狀、根因、解法、驗證狀態 |
| `references/servers.example.yml` | 使用者層設定範例（server、權杖變數名、agent 路徑、已快取 Gradle 版本） |
| `references/ci-tools.example.yml` | 專案層設定範例（repos 清單、pipeline 參數、基準分支） |
| `references/ci-init.gradle` | Gradle CI init script |
| `references/pipeline-templates/` | 五個 pipeline 範本 |
| `references/e2e-layering.md` | 測試分層與 E2E 執行原則 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ci-discover.sh` | 第 0 步：唯讀探索既有設定並建議下一步 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh` | Gitea → ADO 單一 repo 單向快轉同步 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/sync-all-repos.sh` | 依 `.ci-tools.yml` 同步所有 repo（整批把關、依賴先推） |
| `${CLAUDE_PLUGIN_ROOT}/scripts/ci-triage.sh` | 首次建置失敗分類：抽出失敗類別、產生本機重跑與基準比對指令 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/lib/ci_config.py` | 兩層設定檔的讀取器（無外部依賴） |
