---
slug: ctx-save-v3
stage: plan-review
generated: 2026-04-21
reviewers:
  - logic-reviewer (Opus)
  - quality-reviewer (Sonnet)
  - security-reviewer (Opus)
---

# ctx-save v3 程式碼審查報告

## 審查日期

2026-04-21

## 審查範圍

35 個檔案（28 個新增 + 7 個修改），約 10,164 行；範圍對應 `.spec/ctx-save-v3/files.md`。

三位 Reviewer 以 Agent subagent 平行審查（Agent Teams 受「一人一團隊」限制，改以 parallel Agent tool 達成同等配置），由 Leader 交叉彙整。

## 統計

| 類別 | 🔴 嚴重 | 🟡 建議 | 🟢 良好 |
|------|---------|---------|---------|
| 邏輯正確性 | 5 | 7 | 6 |
| 程式碼品質 | — | 11 | 6 |
| 安全與效能 | 2 | 14 | 11 |
| **合計** | **7** | **32** | **23** |

> 安全類「嚴重」採用較嚴格標準：只有「會把敏感資料外洩、或多租戶互踩」的兩項列為 🔴，其餘 Medium/Low 列入 🟡。
>
> 更新紀錄（2026-04-21 修復階段）：C5 經複核屬**誤判** — `AskStrategy.render()` 是純函式，只回一段 stderr 訊息字串，**並未**呼叫 input() 等互動 API，也不存在「等 15 秒 timeout」的路徑。故將 C5 自 🔴 移除，合計 🔴 從 8 下修為 7。

---

## 🔴 嚴重問題

### [C1] `--db` 旗標在 ctx-analyze.py / ctx-distill.py 不存在，Web 端點會靜默失敗
- **檔案**：`plugins/ctx-save/skills/ctx-save/scripts/ctx-viewer.py:3411`、`:3434`
- **Reviewer**：logic
- **問題**：`_handle_diagnoses_run` 與 `_handle_save_distill` 用 `subprocess.run([..., "--db", db_path, ...])` 呼叫兩支 CLI，但這兩支 CLI 只認 `CTX_SAVE_DB_PATH` 環境變數，並未實作 `--db`。使用者從 Web Viewer 按「Run analysis」或「Distill」會以為成功（HTTP 200），實際 CLI 執行時 argparse 報錯並 exit 非 0，前端拿到模糊錯誤。
- **建議**：兩擇一 —（A）在 ctx-analyze.py / ctx-distill.py 的 argparse 補上 `--db` 參數並優先於環境變數；（B）Viewer 端改用 `env={"CTX_SAVE_DB_PATH": db_path, **os.environ}`。建議採 B，介面一致性較佳。

### [C2] `find_latest_unrestored_autodump` 缺少 `project_path` 過濾，會跨專案污染
- **檔案**：`plugins/ctx-save/scripts/ctx-reinject.py:383-386`
- **Reviewer**：logic
- **問題**：SessionStart matcher=compact 觸發時，傳入的 `cwd` 代表當前專案，但查詢未以 `project_path` 過濾，會把**其他專案**最近 7 天內尚未 restore 的 autodump snapshot 抓回來注入。使用者切換到 B 專案卻吃到 A 專案的脈絡。
- **建議**：`find_latest_unrestored_autodump(project_path=cwd)` — 在 Facade 層加上 `WHERE project_path = ?` 條件；查詢 fallback 也要帶此條件。

### [C3] `mark_compact_event_reinjected` 用 snapshot_id 反查可能標錯事件
- **檔案**：`plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py`（`mark_compact_event_reinjected` 實作處）
- **Reviewer**：logic
- **問題**：compact_events 表設計允許同一個 `snapshot_id` 對應多筆事件（autodump 重試、手動 save 後補觸發）。現行以 `snapshot_id` 反查 + `UPDATE ... SET reinjected_at=...` 會同時標到多筆，時序對齊失真；若 autodump 後下一輪又 PreCompact 在同一個 snapshot，會把舊事件也標為「剛 reinject 完」。
- **建議**：`mark_compact_event_reinjected(event_id)` 改為**以 event_id 為主鍵**；reinject hook 取回 snapshot 時同時取 `event_id`（`find_latest_unrestored_autodump` 回傳結構加上 event_id），再 UPDATE 只動該列。

### [C4] `list_compact_events` 空結果未做 fallback，Timeline 初次使用會 500
- **檔案**：`ctx-db.py` `list_compact_events` Facade 方法
- **Reviewer**：logic
- **問題**：若 M4 migration 未跑或使用者升級但尚未觸發過 compact，`context_compact_events` 表為空，Viewer 的 `/api/v3/compact-events` 直接 `SELECT` 可過，但部分分支（如 JOIN `context_saves`）在表空時會丟 `IntegrityError`（依 SQLite 版本）。
- **建議**：Facade 空表時回傳 `[]` 而非拋錯；在 handler 層 try/except 明確回 `200 []`，避免 500 把前端打死。

### [C5]（已撤回 — 誤判）
- **原描述**：「Ask 策略會卡 15s timeout」。
- **複核結論**：`AskStrategy.render()` 只回傳一段 markdown 字串到 stderr，沒有 input() / readline / select 等阻塞呼叫，也沒有 timeout 機制；Ask 模式本來就是「注入提示讓使用者下次自行觸發 `/ctx-save restore --latest-compact`」，行為與建議修復方案相同。
- **後續動作**：不修復；logic reviewer 下次審查時請實際 Read 該段程式碼確認行為後再判定嚴重度。

### [C6] 多條 `restore_strategy` 切換後 cooldown 檔未清除
- **檔案**：`ctx-reinject.py` cooldown 檢查段
- **Reviewer**：logic
- **問題**：`/tmp/claude/ctx-reinject-cooldown` 10 分鐘 cooldown 是全域的，使用者若在 off ↔ full 間切換，cooldown 會卡掉新策略的第一次 reinject；符合「不要連續打擾」但違反「使用者剛改設定就試試看」的直覺。
- **建議**：cooldown 檔路徑加入 strategy 版本戳（如 `ctx-reinject-cooldown-{strategy}`），或在 ctx-config.py 改 `restore_strategy` 時一併 rm cooldown。

### [S-4] DB 檔與 WAL/SHM 缺 0600 權限，多使用者主機可被讀取
- **檔案**：`ctx-db.py` `_ensure_db()` / migration 初始化段
- **Reviewer**：security
- **問題**：`~/.ctx-save/context.db` 建立後沒有 `os.chmod(path, 0o600)`。WAL mode 衍生的 `-wal` / `-shm` 同樣由 SQLite 以預設 umask 建立。多使用者 macOS/Linux 主機上其他帳號可讀取，snapshot 內容含原始對話（含可能的 API key、密碼、客戶資料）。
- **建議**：DB / WAL / SHM 三檔建立後立即 chmod 0600；`~/.ctx-save/` 目錄 chmod 0700；並在 README 增加「此 DB 含未清洗對話，勿在共用主機放寬權限」提示。

### [S-7] autodump 未做密鑰/憑證清洗，原始 transcript 直接入庫
- **檔案**：`ctx-autodump.py` 記錄 transcript tail 段；`ctx-reinject.py` 注入段
- **Reviewer**：security
- **問題**：autodump 抓 transcript 最後 N 則訊息原封不動寫入 `context_saves.content`，之後在 SessionStart=compact 時又被讀出來 stderr 注入回 Claude。若使用者對話中貼過 `sk-...`、AWS key、資料庫密碼、PAT，這些字串會：(a) 永久落地到 DB；(b) 在每次 reinject 時重新出現在 Claude 看得到的 stderr；(c) 被 Viewer 頁面顯示。
- **建議**：新增 `scripts/redact.py`（stdlib re），最少涵蓋：`sk-[A-Za-z0-9]{20,}`、`AKIA[0-9A-Z]{16}`、`ghp_[A-Za-z0-9]{36}`、`xox[baprs]-[A-Za-z0-9-]+`、JWT 三段式、`password["']?\s*[:=]\s*["']?\S+` 等 pattern，對 content 做 in-place 替換為 `[REDACTED:kind]`。寫入 DB 前先過一次；reinject 前再過一次（守第二道防線，避免舊資料外洩）。必要時在 Web Viewer 顯示時也加一次。

---

## 🟡 改善建議

### 邏輯類（logic-reviewer）

- **[S1]** `dedup.py` 的 Budget 層門檻 6 條 findings 固定寫死，建議提為 config（`max_findings_per_report`），與 claudemd_bloat 的閾值 style 統一
- **[S2]** `classifier.py` 遇到 `tool_use_id` 對不到 `tool_result` 時目前 silent skip，建議至少寫 stderr warning 並在 renderer 輸出「N 個 tool 事件無法對齊」提醒
- **[S3]** `ctx-distill.py` heuristic 對「連續相同 tool_result」的折疊閾值寫死 3 次，建議改為 `len > 3 且 相似度 > 0.9`，避免把「相同 URL 但內容不同」的 WebFetch 誤折
- **[S4]** `ctx-viewer.py` SVG pie chart 用固定色盤，五類 token 顏色對色盲使用者辨識度差，建議帶上 pattern fill 或 label 輔助
- **[S5]** `test_rules.py` 七情境未覆蓋「rules 之間交互影響」（例如 duplicate_read + claudemd_bloat 同時觸發時 dedup 的行為），建議補一個 combined fixture
- **[S6]** `ctx-reinject.py` 的 Summary 策略直接截 content 前 N 字，摘要效果差；建議借用 ctx-distill.py 的 rule-based summarizer（可直接 import），產出品質提升
- **[S7]** RestoreStrategy Full 模式在 `max_reinject_bytes < len(content)` 時靜默截斷，建議 stderr 多一行 `[ctx-save] content truncated: N/M bytes`

### 品質類（quality-reviewer）

- **[Q1]** `ctx-reinject.py:312-318` 用 `"string" + var + "string"` 拼字串，同檔其他地方與 `ctx-autodump.py` 全用 f-string；請統一為 f-string
- **[Q2]** `ctx-db.py` 型別註記仍用 `Dict[str, Any]`、`List[Tuple[...]]`（Python 3.8 style），analyze 模組已用 `dict[str, Any]`、`list[tuple[...]]`（3.9+ style）；plugin 既定 Python 3.8 最低支援則 analyze 該降級，否則 ctx-db 該升級，擇一
- **[Q3]** `ctx-viewer.py` 單檔 3617 行，專案 `coding-style.md` 建議 ≤ 800 行；雖然 skill 已允許「單一 RequestHandler 不拆檔」但至少在檔頭加一段 `# ARCHITECTURE NOTE: intentional monolith, split path map below` 並附章節目錄
- **[Q4]** `renderer.py:25` 的 `Report` dataclass 未加 `frozen=True`；其他 DTO (`Event`/`Finding`/`TokenBreakdown`/`SessionBaseline`) 都有
- **[Q5]** `rules/*.py` 六支 rule 的 docstring 格式不一致：有的用 `"""..."""` 一行，有的多行；統一為多行格式並含 `Returns:` 段
- **[Q6]** `tests/_loader.py` 的 `load_script` 函式缺 docstring 解釋為何需要此 helper（hyphenated filename），新人維護時不易理解
- **[Q7]** `analyze/classifier.py:89` 有一個 magic number `500`（short turn 閾值），建議提為模組常數 `SHORT_TURN_TOKEN_THRESHOLD = 500`
- **[Q8]** `ctx-config.py` DEFAULT_CONFIG 結構有註解但 VALID_RESTORE_STRATEGIES 沒有，兩者風格不一
- **[Q9]** `ctx-distill.py` 511 行單檔，含 `DistillRule` enum + 六個 rule + 主流程，可拆為 `distill_rules.py` + `distill.py`；然則 skill 未強制，列為建議
- **[Q10]** `tests/run_all.py` 用 `unittest.TestLoader().discover()`，失敗時輸出難定位；建議改 `unittest.main(verbosity=2, exit=False)` 或至少印出 loaded test names
- **[Q11]** Commit 訊息（若已 commit）是否遵循 conventional commits 格式待確認；plan-build 階段通常未 commit，M5 打磨階段再一起

### 安全類（security-reviewer）

- **[Sec-1]** `ctx-viewer.py` 搜尋 API 的 LIKE 查詢未 escape `%` / `_`，使用者輸入 `50%` 會匹配全部；`ctx-db.py` search_saves 實作處建議 `q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")` 且 SQL 加 `ESCAPE '\\'`
- **[Sec-2]** Web Viewer 綁 `127.0.0.1:29898`，已有 `_reject_cross_origin` 擋 Origin/Referer，但同一台多使用者時其他本地 shell 仍可 `curl`；建議加入「啟動時產生 session token 寫到 `~/.ctx-save/viewer.token` chmod 0600，前端 fetch 自動帶 header，request handler 驗 token」
- **[Sec-3]** `CTX_PROJECT` / `CTX_SAVE_DB_PATH` env var 未驗證是否落在使用者可信路徑；建議白名單：`CTX_SAVE_DB_PATH` 必須在 `$HOME/` 底下或用 `os.path.realpath` 後檢查非 symlink 到系統目錄
- **[Sec-4]** DELETE `/api/v3/diagnoses/<id>` 以 CSRF token 保護但沒 rate limit；攻擊者若拿到 token 可一次刪光
- **[Sec-5]** POST body 用 `MAX_BODY_BYTES` 限制單請求，但無總頻率限制；Distill CLI 可能被反覆觸發耗 CPU
- **[Sec-6]** `ctx-reinject.py` 在 stderr 輸出 snapshot 內容，若 terminal 設定被側錄（screen recording、shell history、tmux copy mode），敏感內容會外擴；建議 README 註明此風險
- **[Sec-7]** `ctx-autodump.py` 的 `read_statusline_context_pct()` 若 statusline 檔為攻擊者可控（shared dev box），可注入惡意值；目前 int() 會擋大多情境，但建議值域鎖定 0-100
- **[Sec-8]** `hooks.json` 的 `ctx-reinject.py` timeout 15s 雖已設，但 SQLite lock 情境若長於 15s（backup 中）會被 kill 掉；建議改 5s 並 fall back 到 `OperationalError` 快速 exit 0
- **[Sec-9]** WAL checkpoint 未主動做，長時間使用 WAL 會長到數百 MB；建議每次 save 後非同步 `PRAGMA wal_checkpoint(TRUNCATE)`
- **[Sec-10]** `ctx-viewer.py` 的 SVG 渲染用字串拼接未 escape `<` / `>` / `&`；雖然 token 值為 int 不會注入，label 來自 classifier 輸出也是白名單五類，目前安全但未來擴充時易破功；建議加 `xml_escape` helper
- **[Sec-11]** `subprocess.run([...], timeout=...)` 未指定 `shell=False` 明示，雖然預設就是 False，為團隊新人可讀性建議補上
- **[Sec-12]** `_loader.py` 動態 import 使用 `spec_from_file_location`，若測試 fixture 路徑被控可 load 任意 .py；測試目錄不可能被攻擊，列為低風險
- **[Sec-13]** ctx-analyze CLI 讀 `~/.claude/projects/**/*.jsonl`，glob 無深度限制，在極深目錄會慢；建議 `rglob` 改 `glob` + 已知路徑
- **[Sec-14]** `_handle_diagnoses_run` / `_handle_save_distill` 用 subprocess 啟動 Python 解譯器，未指定 `sys.executable`，若使用者 `PATH` 被 hijack 會跑到惡意 python；建議 `subprocess.run([sys.executable, script_path, ...])`

---

## 🟢 良好實踐

### 邏輯類

- **[G-L1]** SessionStart matcher 拆 startup/resume/compact 清楚分工，compact 分支獨立腳本易於測試
- **[G-L2]** Immutable DTO 設計讓 pipeline 五階段各司其職，scanner 輸出 freeze 後下游無法誤改
- **[G-L3]** RestoreStrategy Protocol 介面使未來新增策略（如 `persistent_only`）零侵入
- **[G-L4]** 六條 rule pure function ≤ 60 行，單測覆蓋容易、修改風險低
- **[G-L5]** Migration idempotency 測試實際跑兩次驗證，比「肉眼審 SQL」可靠
- **[G-L6]** 80 測試 0.261s 完跑，快到可以放 pre-commit hook

### 品質類

- **[G-Q1]** 全模組繁體中文註解一致
- **[G-Q2]** analyze/rules/registry.py 採 decorator 註冊，新增 rule 只需一檔一 decorator
- **[G-Q3]** tests/fixtures 真實的 JSONL 不是人造假資料，測試貼近實際情境
- **[G-Q4]** Web Viewer 前端 hash router 零依賴，刷新不斷 tab
- **[G-Q5]** db.md / arch.md / files.md 分層清晰，審查時易對齊
- **[G-Q6]** `_loader.py` 明確解決 hyphenated filename import 問題，比改檔名用底線的做法更尊重 plugin 既有慣例

### 安全類

- **[G-S1]** SQL 查詢全數使用 parameterized query（`?` placeholder），無字串拼接
- **[G-S2]** Hook script 一律 `exit 0` + 錯誤寫 `~/.cache/ctx-save/*-error.log`，不因 bug 弄掛 Claude Code
- **[G-S3]** `_reject_cross_origin` 同時檢查 Origin 和 Referer，符合 OWASP 建議
- **[G-S4]** CSRF HMAC confirm_token 有時效性與 per-session secret
- **[G-S5]** `MAX_BODY_BYTES` 限制避免 Viewer 被大 POST 打爆記憶體
- **[G-S6]** `~/.cache/ctx-save/ctx-reinject-cooldown` 用 flag file 而非 env var 傳狀態，避免 leak
- **[G-S7]** `ctx-config.py` VALID_RESTORE_STRATEGIES 白名單驗證，避免不明字串灌入設定
- **[G-S8]** WAL mode 開啟，避免 reader/writer lock 競爭
- **[G-S9]** subprocess 全用 list 形式（非 shell string），無 command injection 面向
- **[G-S10]** `os.path.expanduser` 處理 `~`，不自行拼 `$HOME`
- **[G-S11]** 全數產出零 pip 依賴，攻擊面最小化

---

## 交叉審查發現（Leader 彙整）

三位 Reviewer 獨立審查後，Leader 交叉比對，發現以下**跨類別關聯**：

### X-1: [C1 + Sec-14] Viewer 的 subprocess 呼叫同時有邏輯 bug 與安全 smell
- Logic 抓到 `--db` 旗標不存在（會 silent fail）；Security 抓到未用 `sys.executable`（路徑 hijack 風險）。
- **建議整併修法**：將 `_handle_diagnoses_run` / `_handle_save_distill` 兩 handler 改寫為 `subprocess.run([sys.executable, script_path, ...], env={"CTX_SAVE_DB_PATH": db_path, **os.environ}, timeout=30, check=False)`，一次修兩個問題。

### X-2: [C2 + S-7 + Sec-3] 跨專案污染 + 密鑰外洩 + 路徑未驗證 三者疊加
- Logic 的 C2（缺 project_path）讓 A 專案的 autodump 注入到 B 專案；Security 的 S-7（未清洗密鑰）意味著 A 專案的 API key 可能注入到 B 專案；Sec-3（路徑未驗證）則讓攻擊者可設 `CTX_PROJECT=/etc` 觸發意外路徑。
- **風險升級**：單看 C2 是「體驗問題」，單看 S-7 是「資料落地問題」，三者疊加是**跨專案憑證洩漏**路徑 — 建議列為 M1 必修的壓軸 bug。
- **合併修法順序**：先修 C2 加 project_path 過濾，再加 S-7 redact 層，最後在兩個關鍵入口（autodump 寫入、reinject 注入）都要驗證 project_path `realpath` 落在 `$HOME/` 底下。

### X-3: [C6 + Sec-8] Cooldown + timeout 的互動未測試
- Logic 的 C6（cooldown 版本戳）與 Security 的 Sec-8（15s timeout 太長）都落在 ctx-reinject.py 啟動段。
- **測試缺口**：`test_reinject_contract.py` 三情境都是 exit 0 快速路徑，沒有「SQLite lock + cooldown 檔存在」的複合情境測試。
- **建議**：補 `test_reinject_cooldown.py`，含 cooldown 存在時 exit 0 + 切換 strategy 後 cooldown 失效兩案例。

### X-4: [Q3 + Sec-2] Viewer 單檔過大 + 缺 auth 皆是「未拆」的症狀
- Viewer 3617 行單檔是邏輯未拆；缺 session token 是安全邊界未拆。
- 兩者都反映 Viewer 是「驗證用原型長大的產物」，建議 M5 打磨階段做一輪結構性重構（拆 `viewer/handlers/`, `viewer/auth.py`, `viewer/svg.py`），順手加 token 層。

---

## 後續動作建議

| 優先級 | 項目 | 對應 Finding |
|--------|------|-------------|
| P0（M1 必修） | C1 `--db` 旗標、C2 跨專案污染、S-7 密鑰清洗、S-4 DB chmod | 資料正確性 + 安全底線 |
| P0（M1 必修） | C3 mark_reinjected 標錯、C4 空表 500 | 閉環核心正確性 |
| P1（M2 前完成） | C5 Ask 策略降級、C6 cooldown 版本戳、Sec-2 Viewer token | 使用者體驗 |
| P2（M5 打磨） | Q3 Viewer 拆檔、Q2 型別註記統一、S1-S7 七項 logic 建議 | 可維護性 |
| P3（文件） | Sec-6 README 風險提示、X-2 的合併修法順序放 log.md | 知識沉澱 |

---

## 審查方法說明

本次因 Leader 同時擁有 `ctx-save-v3` 開發 team 未清理，Agent Teams 模式被 runtime 拒絕（`Already leading team`），改以 **3 個 parallel Agent tool subagent** 達成同等 3 人審查效果。副作用：三位 Reviewer 未直接互相分享發現，交叉審查段由 Leader 在收集完三份報告後人工彙整（見「交叉審查發現」一節）。

本次審查覆蓋範圍與 token 成本：
- Logic Reviewer（Opus）：164,985 tokens / 38 tool uses / 231s
- Quality Reviewer（Sonnet）：91,268 tokens / 46 tool uses / 175s
- Security Reviewer（Opus）：155,378 tokens / 41 tool uses / 253s
- 合計：411,631 tokens / 125 tool uses

審查未跑實際 runtime，屬靜態閱讀 + AST 推理。部分 finding（尤其是 C4 空表 500、Sec-9 WAL 膨脹）建議 `/plan-verify` 進一步以實機驗證。

---

## 第二輪快速審查（P0 修復驗收）— 2026-04-24

**模式**：`--quick`（單一 Opus subagent，聚焦 P0 修復的 5 個核心檔案）

### 統計

| 類別 | 🔴 嚴重 | 🟡 建議 | 🟢 良好 |
|------|---------|---------|---------|
| P0 修復驗收 | 0 | 3 | 9 |

### 🟡 建議（不阻擋合併）

**Y-1 — `_apply_secure_permissions` TOCTOU 微視窗**
- **檔案**：`ctx-db.py`
- **問題**：`db_path.exists()` 檢查到 `chmod(0o600)` 之間存在微秒級視窗，SQLite 新建 DB 預設 umask 可能是 0644
- **影響**：共享主機場景理論上可被旁人在該瞬間讀到空 DB，實務風險極低
- **建議**：可用 `os.open(..., O_CREAT|O_RDWR, 0o600)` 預建檔案；不阻擋合併

**Y-2 — cooldown 檔位於 `/tmp/claude/`**
- **檔案**：`ctx-reinject.py`
- **問題**：`/tmp` 為世界可寫目錄，其他使用者可讀取 session 活動
- **說明**：此為既有設計（非 P0 修復引入）。C6 修復本身邏輯完全正確
- **建議**：未來將 cooldown 移至 `~/.cache/ctx-save/`（M5 打磨期）

**Y-3 — `redact_secrets` None 回傳值違反型別標註**
- **檔案**：`redact.py`
- **問題**：`if not text: return text, 0` 當 `text=None` 回傳 `(None, 0)`，違反 `Tuple[str, int]` 標註
- **說明**：兩個整合點（autodump + reinject）已用 `or ""` 防護，不影響功能
- **建議**：改為 `return text or "", 0`；一行修正，不阻擋合併

### 🟢 良好實踐

- C1 — Viewer subprocess env var 修復，簡潔正確，`os.environ` 合併不覆蓋 PATH
- C2 — `_resolve_project_path()` 與 ctx-db.py 平行設計，邏輯一致
- C3 — event_id 優先 + snapshot_id fallback 雙保險，向後相容
- C4 — 三層防禦（exists / has_table / OperationalError），全場景覆蓋
- C6 — `session_id:strategy` 複合鍵，strategy 切換後立即生效
- S-4 — WAL 後綴正確（`-wal`/`-shm`），PRAGMA 後才 chmod，時機正確
- S-7 — 雙清洗防禦縱深，pure function，12 類 pattern 無 ReDoS 風險
- 全檔 stdlib only、繁體中文、參數化查詢、永不中斷 Claude Code 契約

### 結論

**通過** — 7 項 P0 修復全數正確，未引入新 bug。3 項建議均為強化性質，不阻擋合併。
