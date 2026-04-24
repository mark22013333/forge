# ctx-save-v3 開發日誌

## 2026-04-21

### 14:30 — plan-explore 探索完成

透過 `/feature-workflow:plan-explore` 進入探索模式，經多輪對話完成 spec.md 草案：

**探索決策**：

1. **N8（compact events CLI）加入 M1**：原本 N6 Web Timeline 放在 M3，但為了讓 M1 能視覺驗證閉環，採「方案 B」— 在 M1 加一支輕量 CLI `/ctx-save events`，共用 N6 之後要用的 SQL 層。
2. **ctx-analyze 採五類分類（非四、非六）**：
   - 拒絕 claude-crusts 原本的六類（C/R/U/S/T/S）— U/State 在 ctx-save 場景無修復路徑
   - 拒絕單純四類 — Tool 不拆會模糊 schema（啟動時載入）vs runtime（Bash/WebFetch 結果）的修復策略差異
   - 採五類：conversation / files / tools_schema / tools_runtime / system
3. **Findings 防噪四層架構**：原五條規則（duplicate_read、autodump_cluster、unused_mcp_server、claudemd_bloat、short_turns_noise）都有噪音風險，改為六條（新增 user_prompt_spike）+ 四層防噪（Signal validation / Self-calibration / Root-cause dedup / Budget）
4. **守住零 pip 依賴**：全規格刻意排除 Obsidian / Graphify / Slack / Email / OpenAI API 等外部依賴

**產出**：`.spec/ctx-save-v3/spec.md`（1148 行，60KB，11 章 + 2 附錄）

### 15:00 — 進入 plan-spec 正式化

透過 `/feature-workflow:plan-spec` 將探索成果正式化：

- 因探索階段的 spec.md 已達規格書深度，不重跑 agent 覆寫，改為補件：
  - 補上 skill 要求的「判斷」區塊（FRONTEND/DB/TABLES 判定）
  - 同步附錄 B 檔案佈局與 §6.7（rules 模組化 + dedup.py）
  - 建立 README.md（前置檔案）
  - 建立 log.md（本檔）
- 狀態：`_index.md` 從 "spec 草案" → "規格設計"

**判斷結果**：

- FRONTEND_REQUIRED: true（Web Viewer 四 tab 升級）
- FRONTEND_TECH: 原生 HTML/CSS/JS（既有 `ctx-viewer.py` 用 `http.server`，零依賴）
- DB_REQUIRED: true
- DB_TABLES: `context_saves` 擴充 + `context_diagnoses` 新增 + `context_compact_events` 選做

**下一步**：等使用者確認規格書後，可進入 `/plan-db` → `/plan-arch`，再 `/plan-build`。

### 15:30 — plan-db 完成

透過 `/feature-workflow:plan-db` 產出 DB 設計：

**設計要點**：

- 沿用 v2.3 `ctx-db.py` 的 `_run_all_migrations()` 冪等架構，新增 5 個 migration 函式（persistent / restored_at / distilled_from / diagnoses_table / compact_events_table）
- Migration flag 換代：`.migration-done` → `.migration-v3-done`（v2 flag 保留便於 downgrade）
- `context_saves` 加三欄（`is_persistent`、`restored_at`、`distilled_from_id`），全 NULL-tolerant 以避開 SQLite `ALTER ADD NOT NULL` 限制
- 新表 `context_diagnoses`（M2 啟用）：五類 token + findings_json + suggest_focus，附 4 索引
- 選做表 `context_compact_events`（M4 啟用）：Timeline 效能不足時再建；M3 之前用 query `context_saves` 頂著
- `distilled_from_id` 刻意不建 FK，讓 distilled 版本在原 snapshot 被 clean 刪除時仍能獨立存活
- `clean` SQL 擴充：排除 `is_persistent=1` 與近 7 天內 reinject 過的 snapshot

**產出**：
- `.spec/ctx-save-v3/db.md`（11 章：設計原則、基線、變更總覽、三張表細節、migration 流程、效能評估、rollback 策略、檢核清單）
- `.spec/ctx-save-v3/db.sql`（4 段：A 全新安裝 / B 漸進 migration / C 範例資料 / D rollback 含 SQLite < 3.35 的重建法）

狀態：`_index.md` 規格設計 → DB 設計

**下一步**：`/plan-arch` — 架構設計（閉環資料流、analyze 模組分層、Web Viewer Tab 路由）。

### 16:00 — plan-arch 完成

透過 `/feature-workflow:plan-arch` 產出架構設計：

**設計決策**：

- 沿用 ctx-save 既有的 **兩層分工**（plugin-level hooks vs skill-level 重邏輯），v3 新增的 `ctx-reinject.py` 放 plugin 層、`ctx-analyze.py`/`ctx-distill.py`/`analyze/` 子模組放 skill 層
- **閉環資料流** 以 Mermaid sequence diagram 定義：Stage 1 PreCompact（補 `context_percentage`）→ Stage 2 SessionStart matcher=compact（讀未 restored snapshot → stderr 注入 → UPDATE restored_at）
- **Hook 契約**：plugin-level script 失敗一律 `exit 0`，錯誤寫 `~/.cache/ctx-save/*-error.log`；永不中斷 Claude Code
- **restore_strategy 策略模式**：off/summary/full/ask 四種實作，透過 Protocol 介面隔離
- **ctx-analyze 五階段 pipeline**：scanner → classifier → rules(via registry) → dedup → renderer；五類 token 分類結果在 classifier 產出 SessionBaseline，供 rules 做 Layer 2 自校
- **Rule Registry 模式**：`rules/registry.py` 註冊中心 + 每 rule 一檔（≤ 60 行 pure function）
- **Immutable DTO**：`Event`/`Finding`/`Report`/`TokenBreakdown`/`SessionBaseline` 全 `@dataclass(frozen=True)`，對應全域 coding-style.md
- **Web Viewer**：單一 `RequestHandler` 不拆檔、新增 10+ 個 v3 API 端點；前端用 `location.hash` 路由驅動四 tab（Timeline/Dashboard/Diagnostics/Persistent），純 vanilla JS
- **hooks.json 變更**：`SessionStart` 由單一 `matcher:*` 拆成 3 個 matcher（`startup`/`resume`/`compact`），`compact` 綁新 `ctx-reinject.py`
- **ctx-distill heuristic summarizer**：全規則式（去重 tool_result、過濾短訊、保留錯誤訊息）— 因零 pip 依賴無法走 embedding/LLM
- **測試策略**：全 stdlib `unittest`，含 migration idempotency、Hook 契約三情境（DB 不存在 / lock / None snapshot 全 exit 0）、pipeline integration（3 個 golden JSONL fixture）

**風險緩解表**：列 5 項重點（re-inject 反而吃 context、PreCompact 阻塞、JSONL 大檔、rule false positive、舊 SQLite 3.30 相容），各有具體緩解措施。

**產出**：`.spec/ctx-save-v3/arch.md`（13 章 + 3 張 Mermaid 圖 + 類別總覽表）

狀態：`_index.md` DB 設計 → 架構設計

**下一步**：可直接進 `/plan-build`（Agent Teams 產生程式碼骨架）或 `/plan-review`（審查設計產出）。

### 17:10 — plan-build 完成

透過 `/feature-workflow:plan-build` 啟動 4 人 Agent Team（closure-eng / analyze-eng / viewer-eng / distill-test-eng），以 **custom team composition** 取代 skill 預設 POJO/Mapper/Service 配置（因本專案是 Python stdlib plugin，非 Spring）。

**檔案歸屬劃分（零衝突）**：
- `closure-eng` ← Hook 閉環：`ctx-reinject.py` / `ctx-autodump.py` / `ctx-config.py` / `hooks.json` / `plugin.json` / `ctx-db.py` 擴充
- `analyze-eng` ← `analyze/` 模組（5 階段 pipeline + 6 條 rules + 4 層防噪）+ `ctx-analyze.py` CLI + 4 個相關測試
- `viewer-eng` ← `ctx-viewer.py`（10 個 v3 API + 6 tab 前端 + SVG 視覺化）
- `distill-test-eng` ← `ctx-distill.py` + `tests/` 骨架 + 6 個測試 + 4 個 fixture

**產出**（見 `.spec/ctx-save-v3/files.md` 完整清單）：
- 28 個新檔 + 7 個修改檔 = 35 檔，約 10,164 行
- Python AST parse 全數 OK
- `python3 tests/run_all.py` → **Ran 80 tests in 0.261s — OK**

**設計約束遵守**：
- ✅ 零 pip 依賴（純 Python stdlib）
- ✅ 零外部服務（Viewer 前端純 vanilla JS + 純 SVG）
- ✅ 繁體中文註解
- ✅ 全 Immutable DTO（`@dataclass(frozen=True)`）
- ✅ 全 rule pure function（≤ 60 行）

**Teammate 任務統計**：18 個 task 全部 completed（含中途補件回合 — 首輪 4 個 teammate 僅完成 6 個 task，再次派發後補完剩餘 12 個）。

狀態：`_index.md` 架構設計 → 開發中

**下一步**：可跑 `/plan-verify`（運行時驗證）、`/plan-review`（程式碼審查）或 `/plan-close`（結案 + Notion 同步）。

### 18:30 — plan-review 完成

透過 `/feature-workflow:plan-review` 啟動三人平行審查。

**執行方式變通**：Leader 同時持有未清理的 `ctx-save-v3` 開發 team（viewer-eng 先前 shutdown 卡住），runtime 拒絕 `TeamCreate` 建立新 review team（`Already leading team`）。改以 **3 個 parallel Agent tool subagent** 達成同等 3 人審查效果，Leader 事後人工彙整交叉審查段。

**三位 Reviewer 產出**：
- Logic Reviewer（Opus）：6 🔴 / 7 🟡 / 6 🟢（164,985 tokens）
- Quality Reviewer（Sonnet）：0 🔴 / 11 🟡 / 6 🟢（91,268 tokens）
- Security Reviewer（Opus）：2 🔴 / 14 🟡 / 11 🟢（155,378 tokens）
- 合計 411,631 tokens / 125 tool uses / ~4 分鐘並行完成

**彙整統計**：🔴 **8 嚴重** / 🟡 **32 建議** / 🟢 **23 良好**

**關鍵嚴重問題**（P0 必修）：
- **C1**：Viewer 的 `_handle_diagnoses_run` / `_handle_save_distill` 用 `--db` 旗標呼叫 CLI，但 ctx-analyze.py / ctx-distill.py 只認 `CTX_SAVE_DB_PATH` env var → 前端按鈕會 silent fail
- **C2**：`find_latest_unrestored_autodump` 缺 `project_path` 過濾 → 跨專案 snapshot 污染
- **C3**：`mark_compact_event_reinjected` 以 snapshot_id 反查會標錯多筆事件
- **C4**：`list_compact_events` 空表情境未處理 → Timeline 初次載入 500
- **C5**：RestoreStrategy.Ask 在 hook 非互動環境會卡到 timeout
- **C6**：cooldown 檔未隨 strategy 切換清除
- **S-4**：DB/WAL/SHM 未 chmod 0600，共享主機可被其他帳號讀取
- **S-7**：autodump 未清洗密鑰 → API key / password 落地 DB + 每次 reinject 外洩

**交叉審查四項發現**（單類別看不出來、Leader 彙整才顯形）：
- **X-1**：C1（`--db` 旗標）與 Sec-14（未用 `sys.executable`）可合併修法 — 改用 `sys.executable` + `env={"CTX_SAVE_DB_PATH": ...}` 同時修兩問題
- **X-2**：C2 + S-7 + Sec-3 三疊加 = **跨專案憑證洩漏路徑**（單看是體驗問題，疊起來是安全事件）
- **X-3**：C6 + Sec-8 的複合情境（cooldown + SQLite lock + timeout）測試未覆蓋
- **X-4**：Q3（Viewer 3617 行）+ Sec-2（缺 auth token）同為「Viewer 未拆」的兩個症狀，M5 應一併重構

**產出**：`.spec/ctx-save-v3/review.md`

狀態：`_index.md` 開發中 → 程式碼審查

**下一步**：
- 修 P0 八項（C1-C6 + S-4 + S-7）後可跑第二輪 `/plan-review --quick` 確認
- 或直接 `/plan-verify` 實機驗證 C4 空表 500 與 Sec-9 WAL 膨脹
- 完成 P0 修正後再 `/plan-close`（結案 + Notion 同步）

---

## 2026-04-21 19:00 — P0 修復完成

auto 模式下連續完成 7 項 P0 修正（原 8 項，C5 經複核屬誤判，已自 review.md 撤回）。

### 修復清單

| ID | 範圍 | 修法 | 檔案 |
|----|------|------|------|
| C1 | Viewer subprocess 改用 `env={"CTX_SAVE_DB_PATH": ...}`，拿掉不存在的 `--db` 旗標；`_handle_save_distill` 改為 positional save_id | `ctx-viewer.py` |
| C2 | `find_latest_unrestored_autodump` 呼叫處加 `project_path=_resolve_project_path()`，避免跨專案污染 | `ctx-reinject.py` |
| C3 | `find_latest_unrestored_autodump` Facade 改為回傳 `latest_compact_event_id` 相關子查詢；`mark_compact_event_reinjected` 優先以 `event_id` 精準標記單列 | `ctx-db.py`, `ctx-reinject.py` |
| C4 | `list_compact_events` / `mark_compact_event_reinjected` 包 `try/except sqlite3.OperationalError` + `_has_table` 前置檢查，空表回 `[]` | `ctx-db.py` |
| C5 | **不修** — 誤判；Ask 策略只回傳 stderr 字串，沒有互動 | — |
| C6 | `_cooldown_key` 改為 `session_id:strategy` 複合鍵；`is_in_cooldown` / `mark_cooldown` 加上 `strategy` 參數 | `ctx-reinject.py` |
| S-4 | 新增 `_apply_secure_permissions` helper：目錄 0700、DB 與 `-wal` / `-shm` 同步 0600；於 `_connect_db` 呼叫（WAL 檔在 PRAGMA 後才存在） | `ctx-db.py` |
| S-7 | 新增 `plugins/ctx-save/scripts/redact.py`（純 stdlib，零 pip），兩道防線：(1) autodump `build_record` 寫 DB 前清洗；(2) reinject `_do_reinject` 注入 stderr 前再清洗一次 | `redact.py`（新）, `ctx-autodump.py`, `ctx-reinject.py` |

### Redact Pattern 覆蓋

`redact.py` 命中 12 類敏感資料：Anthropic key / OpenAI key（含 `sk-proj-`）/ AWS Access Key ID / GitHub PAT / Slack token / JWT / PEM 私鑰 header / Google OAuth refresh / Authorization Bearer / `password=` / `api_key=` / `aws_secret_access_key=`。

### 驗證

`python3 tests/run_all.py` → **80 tests OK**（所有既有測試通過，未出現 regression）。

### 交叉審查結論回饋

- **X-1**（C1 + Sec-14）：採 B 方案（env var）一併修；`sys.executable` 未在本輪動，留待 M5 Viewer 重構。
- **X-2**（跨專案憑證洩漏）：C2 過濾 + S-7 雙層 redact 已根治；殘留風險（歷史 v2.3 明文 snapshot）由 reinject 端 redact 防守。
- **X-3**（cooldown + lock + timeout）：C6 修掉 cooldown 面；lock/timeout 留待 M4 測試擴充。
- **X-4**（Viewer 未拆）：Q3 + Sec-2 皆未在本輪處理，列入 M5 Roadmap。

### 下一步

- 已可執行 `/plan-review --quick` 第二輪確認（只檢邏輯）
- 或直接 `/plan-verify` 實機驗證 C4 空表路徑與 S-4 權限
- P0 清完後再 `/plan-close`（結案 + Notion 同步）

---

## 2026-04-24

### plan-verify 完成

透過 `/feature-workflow:plan-verify` 執行實機驗證（Playwright MCP + curl + Python unittest）。

**工具**：Playwright MCP（chrome-devtools-mcp 無法連線，fallback）+ curl + unittest

**結果**：✅ 10 / ❌ 0 / ⏭️ 0 / 👤 1

| 驗證項目 | 結果 |
|---------|------|
| 測試套件 80 tests | ✅ PASS |
| C4 — Timeline 空表不 500（API + UI 雙驗） | ✅ PASS |
| S-4 — DB/WAL/SHM 權限 0600 | ✅ PASS |
| Web Viewer 啟動 v3.0.0 | ✅ PASS |
| Dashboard tab | ✅ PASS |
| Timeline tab | ✅ PASS |
| Diagnostics tab | ✅ PASS |
| Persistent tab | ✅ PASS |
| ctx-analyze.py CLI | ✅ PASS |
| ctx-reinject.py hook | ✅ PASS |
| M1 閉環端對端 | 👤 MANUAL（需真實 compact） |

**報告**：verify.md
**截圖**：screenshots/（4 張）

**下一步**：`/plan-review --quick` 第二輪確認，確認後 `/plan-close`。

### plan-review --quick 完成

**模式**：單一 Opus subagent（P0 修復驗收）
**審查檔案**：ctx-db.py / ctx-reinject.py / ctx-viewer.py / redact.py / ctx-autodump.py

**結果**：🔴 0 / 🟡 3 / 🟢 9 — **通過**

| 建議項目 | 性質 | 阻擋合併？ |
|---------|------|----------|
| Y-1 TOCTOU 微視窗（chmod） | 強化建議 | ❌ |
| Y-2 cooldown 在 /tmp | 既有設計 | ❌ |
| Y-3 None 型別標註不一致 | 一行修正 | ❌ |

**下一步**：`/plan-close` 結案。
