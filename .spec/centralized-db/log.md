# 開發日誌

## 2026-04-18 — hotfix（plan-review 的 4 嚴重）

- **範圍**：L-1 / L-3 / L-13 / L-16（review.md 中「綜合建議先修」清單全數修復）
- **修改檔案**：
  - `ctx-viewer.py`
    - **L-13**：`/api/ping` DB busy 改回 503（符合 spec §2.2）；正常時維持 200
    - **L-1**：`_delete_with_fallback` 迴圈每輪 `conn.commit()` 之後補 `BEGIN IMMEDIATE`；rollback 改成顯式 `ROLLBACK` SQL 並吞掉「no transaction」的 OperationalError；soft-delete fallback 同樣補 BEGIN
    - **L-16 後端**：`_handle_batch_delete` 對 `before/after` normalize（`T` → 空白、去尾 `Z`）並以新增的 `DATE_FILTER_RE` 嚴格驗格式；不合法回 400
    - 新增 `import re` + `DATE_FILTER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")`
  - `ctx-db.py`
    - **L-3**：`maybe_auto_migrate()` 在 central_db 已存在時先跑 `_run_all_migrations`（冪等）再往下走，避免 v1 舊 DB 噴 `no such column: deleted_at`
  - `ctx-view-launcher.py`
    - **L-13**：`_ping()` 加 `HTTPError` 分支，503 解析 body 視為「服務已啟動但 DB 忙」交呼叫方判斷，不再當成 down
    - **L-3**：`find_db()` 在 DB 已存在時仍以 `importlib` 載入 `ctx-db.py` 的 `_run_all_migrations` + `_connect_db` 執行 schema migration
  - `ctx-viewer.py`（前端）
    - **L-16 前端**：`collectBatchFilter` 改送 `YYYY-MM-DD 00:00:00` 對齊 SQLite `CURRENT_TIMESTAMP` 的字典序
- **驗證**：
  - 三檔 `ast.parse` 通過
  - CLI smoke（init / save / list-projects / stats / list 10 / illegal session_id → exit 2 / re-init 冪等）全綠
  - `normalize()` 單元驗證 7 組（ISO Z、含 T、純日期、畸形、注入）全綠
  - 端到端 viewer：`/api/ping` = 200；batch-delete dry-run 以 ISO 輸入回傳 filter 已 normalize 為 SQLite 格式；`2026/04/18` 和 SQL 注入字串回 400
- **未處理**：
  - 🟡 改善建議（44 項）全數留待下一輪 refactor（本次只動 🔴 嚴重）
  - L-19 迴歸測試腳本未寫入 repo（本次用臨時 shell 腳本驗證）
- **狀態**：`程式碼審查` → `程式碼審查`（待 /plan-close 決定；可再跑一次 /plan-review 或直接結案）

## 2026-04-18 — plan-review

- **模式**：3 位 Subagent 並行（logic Opus / quality Sonnet / security Opus）
- **範圍**：3 Python 檔案 3655 行
- **統計**：🔴 4 嚴重 / 🟡 44 建議 / 🟢 27 良好
- **Round-3 漏洞**：9/9 全修（S3-1~S3-7 + P3-1 + P3-3 + Q3-4）
- **新發現嚴重**：
  - **L-1** `isolation_level=None` + batch delete 多輪 commit 間無 BEGIN IMMEDIATE → 失去交易保護（quality Q-12/Q-17 同源確認）
  - **L-3** `maybe_auto_migrate` 不跑 schema migration → 舊 v1 DB 指定時直接噴 OperationalError
  - **L-13** `/api/ping` DB busy 回 200 違反 spec §2.2（應回 503）
  - **L-16** 前端 `YYYY-MM-DDT00:00:00Z` 與 SQLite `YYYY-MM-DD HH:MM:SS` 字典序不合 → batch delete 範圍誤判（**資料正確性**）
- **結論**：security 自評「可結案」（3 medium 皆本機邊界）；logic 自評「不可結案」（L-16 影響資料正確性）→ **綜合建議先修 4 項 hotfix 後再 `/plan-close`**
- **報告**：`.spec/centralized-db/review.md`
- **狀態**：`開發中` → `程式碼審查`

## 2026-04-18 — plan-build

- **模式**：3 位 Subagent 並行（非 Agent Teams — 檔案互不重疊且 Python 單語言，Team 協調 overhead 不划算）
- **產出**：
  - `ctx-db.py` 969 行（資料層 + migration + legacy 合併 + CLI）
  - `ctx-viewer.py` 2078 行（Web server + 9 API + HTML/JS 前端）
  - `ctx-view-launcher.py` 608 行（啟動器 + stale PID 清理 + DB path 覆寫）
  - 三檔合計 3655 行
- **契約對齊**：三位 Subagent 各自讀同一份 `arch.md` 作為介面契約；完成後交叉檢查 `CTX_DB._connect_db / _run_all_migrations / list_projects / _validate_session_id` 簽名一致
- **語法驗證**：三檔 `ast.parse` 通過
- **smoke test**（9 個案例全過）：
  - init / save（含 project_path）/ save with illegal category → 正規化為 `custom`
  - list-projects / stats with `by_project` / list with `project_path=` filter
  - illegal `session_id` `"abc;DROP"` → exit 2（驗證輸入驗證）
  - `migrate-legacy --dry-run`（`CTX_SAVE_MIGRATE_ROOTS=/nonexistent`）→ 空結果正常回傳
- **中途事故**：首次 smoke test 跑 `list-projects` 卡 54 秒被 killed
  - 根因：`_scan_legacy_dbs` 用 `Path.home().rglob(".ctx-save/context.db")` 掃整個家目錄（含 Library/Caches/node_modules）
  - 修復：改用 `_legacy_scan_roots()`，限制在 `IdeaProjects / Projects / workspace / code / src`；並提供 `CTX_SAVE_MIGRATE_ROOTS` env 覆寫；新增 `CTX_SAVE_NO_MIGRATE=1` 徹底關閉自動 migration
  - 已於 arch.md §5 migration 時序圖同步更新註解
- **Agent 回報的設計偏離**（已審核接受）：
  - ctx-db 在 legacy 合併時把「rename 舊檔」延後到外層 `COMMIT` 之後（arch.md §5 原寫在 transaction 內）— SQLite 在 transaction 中持有檔鎖無法 rename，此調整保留原本 atomic 語義
  - ctx-viewer 的 HMAC confirm_token 用 `CONFIRM_SECRET + "|" + STARTED_AT` 作輸入（spec.md 原表述較模糊為「以 STARTED_AT 當 key」），以 arch.md §6 為準
- **產出檔案清單**：見 `.spec/centralized-db/files.md`
- **狀態**：`需求分析` → `開發中`
- **後續**：`/plan-verify`（E2E 驗收）→ `/plan-review`（3 人審查）→ `/plan-close`（plugin.json 升版並 commit）

## 2026-04-18 — plan-start

- **觸發動機**：使用者 `/plan-explore` 回報 `http://localhost:29898/` 看不到資料、無法跨 session 檢視、網頁無法識別 DB 來源
- **根因調查**：
  - viewer process 已死（PID 11501 不存在，port 29898 connection refused）
  - 架構為 per-project DB（從 cwd 往上找一個 `.ctx-save/context.db`）→ 本質無法跨專案
  - 前一輪對話紀錄顯示曾完成 v2.1 集中式 DB 設計與實作，但 forge repo `master` 為 clean，`.spec/centralized-db/` 不存在 → artefacts 未落盤
- **Round-3 Agent Teams 審查**：security / quality / logic 三位，皆以 **B-0 Blocker** 回報「本輪修復未落檔」；security 另外發現 S3-1（XSS）、S3-2（CSRF）、S3-7（category XSS）等 7 項 + 3 項效能問題，quality 回報 Q3-4（batch delete filter 不一致）
- **本次決策**：方向 A（集中式 DB）。建立 `feature/ctx-save-centralized-db` 分支 + `.spec/centralized-db/` 本地規劃目錄，依序跑 `/plan-spec` → `/plan-db` → `/plan-arch` → `/plan-build`
- **Notion 狀態**：未註冊（forge 為個人 plugin marketplace，非 Notion 工作區的公司專案）— 採 skill 的 "Notion API 失敗" graceful fallback，`notion_page_id` 留空
