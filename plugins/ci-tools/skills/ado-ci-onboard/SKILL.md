---
name: ado-ci-onboard
description: 引導把自架 Azure DevOps Server（ADO）接上既有 Gitea 專案的 CI：評估、安裝 Server 與 agent、匯入 repo、套 pipeline 範本、以 REST API 驗證建置、Gitea → ADO 單向同步、測試分層。當使用者提到「ado-ci-onboard」、「導入 CI」、「自架 Azure DevOps」、「ADO Server」、「Gitea 同步到 ADO」、「pipeline 範本」、「CI 踩坑」時觸發此 Skill。
---

# ADO CI Onboard — 自架 Azure DevOps Server 導入 CI

把既有 Gitea 專案接上自架 Azure DevOps Server（以下簡稱 ADO）的 CI。Gitea 仍是主要 repo，ADO 只當 CI 鏡像。

本 skill 的內容來自一次實際導入。踩過的坑整理在 `references/pitfalls.md`，每條都有驗證狀態。

---

## 使用守則

1. **專案值不寫進本 plugin**：主機名、網址、專案代號、權杖、帳密都放在使用者專案的 `.ci-tools.yml`（見下方範例）。這個檔案要加進該專案的 `.gitignore`，或確認只放在私有 repo。
2. **不強制推送**：同步只做快轉，分叉就停下來交給使用者決定。
3. **保護分支由使用者自己推**：遇到 `main`、`master`、`production`，只印出指令，不代為執行（踩坑 24）。
4. **保留驗證狀態**：範本與踩坑表標示「未驗證」「估計」「未釐清」的項目，轉述給使用者時照原標示說明，不要改寫成肯定句。
5. **驗證看 API，不看截圖**：建置是否成功，以第 5 章的 REST API 結果為準。

## 範本驗證狀態

| 範本 | 狀態 |
|------|------|
| `pipeline-templates/gradle17.yml` | 流程已在作者環境實測；本檔是去識別化改寫版，改寫後未在 ADO 重新執行 |
| `pipeline-templates/maven8.yml` | **未驗證** |
| `pipeline-templates/maven25.yml` | **未驗證** |
| `pipeline-templates/vue.yml` | **未驗證** |
| `pipeline-templates/python.yml` | **未驗證** |
| `ci-init.gradle` | 已在最小專案（Gradle 8.14.3）實測：覆寫 `failFast`、`forkEvery`、空清單失敗、只跑清單內測試、排除清單、清單帶 BOM |

套用未驗證的範本時，先告訴使用者它沒有跑過，第一次建置後再依第 5 章確認。

## `.ci-tools.yml` 範例（放在使用者專案，不放本 plugin）

```yaml
# .ci-tools.yml — 本專案的 CI 設定（含內部資訊，不要提交到公開 repo）
gitea_url: ssh://git@gitea.example.com/team/project-a.git
ado_url: ssh://ado-host:22/<Collection>/project-a/_git/project-a
ssh_key: ~/.ssh/ado_ci_rsa            # ADO 內建 SSH 只收 RSA（踩坑 4）
branches: [main, uat]                  # 要同步的分支；省略表示全部
protected_branches: [main, master, production]
jdk_home: 'C:\Java\jdk-17'
recent_test_days: 0                    # 0 表示跑全部測試
offline: true
# 權杖不要寫在這裡：用環境變數 ADO_PAT，或系統的密碼管理工具
```

---

## 0. 評估

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
5. **連接埠**：記下精靈設定的連接埠，寫進 `.ci-tools.yml`。
6. **IIS 基本驗證**：停用，否則所有 PAT 都會 401（踩坑 5）。

## 2. 安裝 agent

1. 網頁的「新增代理程式」對話框若沒有內容，改用 REST API `_apis/distributedtask/packages/agent?platform=win-x64` 取得版本與下載網址（踩坑 19）。
2. 同一台機器可以裝多個 agent，各用不同資料夾與名稱。記憶體以每個建置 6～8GB **粗估**（未量測），先開 3～4 個再觀察（踩坑 22）。
3. 在 agent 加上使用者自訂的 capability 標籤，例如 `JDK17`、`JDK8`、`node`、`python`，讓 pipeline 用 `demands` 指定。
4. 安裝 JDK、Node 等工具時注意：
   - agent 以 NetworkService 執行時沒有使用者資料夾，npm 快取要指到可寫路徑（`NPM_CONFIG_CACHE`，踩坑 7）。
   - agent 服務的 PATH 沒有 git，腳本步驟要用 `<agent 目錄>\externals\git\cmd\git.exe`（踩坑 6）。

## 3. 匯入 repo（Gitea → ADO）

1. 準備 CI 專用的 **RSA 4096** 金鑰並加到 ADO（ed25519 不被接受，踩坑 4）。測試連線時加 `-o IdentitiesOnly=yes -o IdentityAgent=none`。
2. 用 bare clone 從 Gitea 取得 repo，**只推分支**（`git push --all`），不要用 mirror 推送。
3. 推完**逐分支比對 SHA**：兩邊各跑 `git ls-remote`，每個分支的 SHA 都要一致。
4. **立刻改預設分支**：ADO 會把第一個收到的分支當預設，常常變成測試分支。用 REST API `PATCH _apis/git/repositories/{id}` 把 `defaultBranch` 改成正式分支（踩坑 18）。

第 2、3 步也可以直接用第 6 章的同步腳本做：新分支會被推上去，保護分支只印指令，最後自動用 `git ls-remote` 比對。

## 4. 套 pipeline 範本

1. 依專案類型選 `references/pipeline-templates/` 裡的範本，並告知使用者它的驗證狀態（見上方表格）。
2. Gradle 專案：把 `references/ci-init.gradle` 複製到專案的 `ci/ci-init.gradle`，pipeline 以 `--init-script` 載入，**不改專案的 build.gradle**。它會：
   - 在 `gradle.projectsEvaluated` 內設定 `failFast = false`（踩坑 10）、`forkEvery = 1`（踩坑 11）
   - 依 `RECENT_TESTS_FILE` 只跑近期修改的測試，清單為空就讓建置失敗（踩坑 12）
   - 套用 `EXCLUDED_TESTS` 排除清單，每筆都要寫原因與移除條件
3. 依 `.ci-tools.yml` 設定參數：
   - `jdkHome`：JDK 路徑
   - `recentTestDays`：近期測試天數（0 表示全部）
   - `offline`：離線開關（預設開）。動態版本依賴在離線時會解析不到（踩坑 15）
4. 明確傳 `-Dorg.gradle.jvmargs=-Xmx3g -XX:MaxMetaspaceSize=1g`，不依賴開發者的個人設定（踩坑 8）。
5. 測試需要的環境變數放在 pipeline 的 `env`，值用 pipeline 變數或 secret（踩坑 9）。
6. 子專案本來就壞的測試（本機也失敗），用 `extraGradleArgs` 傳 `-x <子專案>:test` 暫時排除，並列為待辦（踩坑 13）。
7. `settings.gradle` 引用同層的其他 repo 時，要並排 checkout，並先確認被引用 repo 的分支含有需要的 commit（踩坑 14）。

## 5. 以 REST API 驗證建置

不要只看網頁截圖。用 PAT 呼叫 REST API 取證（PAT 從環境變數讀，例：`curl -u ":$ADO_PAT"`）：

| 要確認的事 | API |
|------------|-----|
| 最近一次建置的結果 | `GET <ADO 網址>/<Collection>/<專案>/_apis/build/builds?definitions=<定義 id>&$top=1` |
| 哪個步驟失敗 | `GET .../_apis/build/builds/<buildId>/timeline`，找 `result` 為 `failed` 的 record |
| 失敗步驟的 log | `GET .../_apis/build/builds/<buildId>/logs/<logId>`（logId 取自 timeline record） |
| 測試統計 | `GET .../_apis/test/runs?buildUri=<build 的 uri>`，看 `totalTests`、`passedTests` 等欄位 |

`api-version` 依伺服器版本指定。

判讀時注意：

- 測試分頁的數字可能和 Gradle 輸出不同（踩坑 20，**原因未釐清**）。建置成敗以 Gradle 為準，數字差異如實回報，不要自行解釋原因。
- 開了近期測試篩選時，確認實際執行的測試數大於 0。`ci-init.gradle` 只擋得住「清單為空」，擋不住「清單有內容但全部對不上」。
- PAT 回 401 時，先查 IIS 基本驗證（踩坑 5）。發 PAT 前先確認它的實際權限範圍（踩坑 23，**範圍細節未釐清**）。

## 6. 同步策略

- **Gitea 是主要 repo，ADO 是 CI 鏡像，只做單向快轉。**
- 使用同步腳本：

  ```bash
  # 預覽（預設，不推任何東西）
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh" \
    --gitea <gitea_url> --ado <ado_url> --key <ssh_key> [--branches main,uat]

  # 確認預覽沒問題後才套用
  "${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh" ... --apply
  ```

  參數從使用者專案的 `.ci-tools.yml` 組出來。

- 預覽會列出每個分支的狀態：`一致`、`可快轉 (領先 N)`、`ADO 領先 (N)`、`已分叉`、`新分支`、`僅 ADO`、`兩邊皆無`。
- `--apply` 的規則：
  - 只推 `可快轉` 與 `新分支`，不用強制推送。
  - 只要有一個選定分支是 `已分叉`，**整批都不推**，結束碼 2。請使用者先在 Gitea 端處理，或用 `--branches` 排除該分支。
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
| `references/pitfalls.md` | 24 條踩坑：症狀、根因、解法、驗證狀態 |
| `references/ci-init.gradle` | Gradle CI init script |
| `references/pipeline-templates/` | 五個 pipeline 範本 |
| `references/e2e-layering.md` | 測試分層與 E2E 執行原則 |
| `${CLAUDE_PLUGIN_ROOT}/scripts/sync-gitea-to-ado.sh` | Gitea → ADO 單向快轉同步腳本 |
