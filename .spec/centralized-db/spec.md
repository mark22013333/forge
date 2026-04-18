# ctx-save 集中式 DB — 技術規格書

- **Feature slug**: `centralized-db`
- **版本目標**: v2.1.0（升級自 v2.0.0 per-project DB）
- **技術棧**: Python 3 標準庫（`sqlite3`、`http.server`、`subprocess`、`pathlib`）
- **相依 skill**: `ctx-save` / `ctx-view` / `ctx-view-stop`
- **規格版本**: 1.0（合併 Round-3 審查修復清單）

---

## 1. 功能範圍

### 1.1 In Scope

| 編號 | 項目 | 影響檔案 |
|---|---|---|
| F-1 | DB 集中化：`{project}/.ctx-save/context.db` → `~/.ctx-save/context.db`（單一 SQLite） | `ctx-db.py`、`ctx-viewer.py`、`ctx-view-launcher.py` |
| F-2 | 每筆記錄強制帶 `project_path`（CLI 端存 `Path.cwd().resolve()`） | `ctx-db.py` |
| F-3 | DB schema 遷移：`context_saves` 增加 `project_path` 欄位（已存在則跳過），加索引 `idx_saves_project` | `ctx-db.py: init_db / migrate_add_project_path` |
| F-4 | Legacy DB 遷移指令：`ctx-db.py migrate-legacy` 掃描 `~` 下 `.ctx-save/context.db` 合併進中央 DB，舊檔改名為 `context.db.migrated-YYYYMMDD` | `ctx-db.py` |
| F-5 | 首次執行 `/ctx-save list`（或任何 read command）自動觸發一次遷移，印一次提示，後續沉默（`~/.ctx-save/.migration-done` 旗標） | `ctx-db.py` |
| F-6 | Viewer header 顯示 `⚙ Context Save Viewer — DB: ~/.ctx-save/context.db — v2.1` | `ctx-viewer.py: HTML_PAGE` |
| F-7 | Viewer sidebar 新增「專案」下拉（option 來自 `/api/projects`），切換即時過濾 | `ctx-viewer.py: HTML_PAGE + /api/projects` |
| F-8 | `/api/sessions` / `/api/search` 支援 `project_path` query param 篩選 | `ctx-viewer.py` |
| F-9 | `/api/ping` 回傳擴充：`{service, version, db_path, project_count, record_count}` | `ctx-viewer.py` |
| F-10 | `/api/stats` 新增 `by_project` 分組統計 | `ctx-viewer.py` |
| F-11 | Viewer WAL + busy_timeout=5000 + `synchronous=NORMAL`，所有 write 端用 `BEGIN IMMEDIATE` | `ctx-viewer.py: get_db / ctx-db.py: _connect_db` |
| F-12 | Launcher 檢測 stale PID 自動清理；支援 `CTX_VIEW_DB` 環境變數覆寫 DB 路徑；新 CLI flag `--db` 保留 | `ctx-view-launcher.py` |
| F-13 | Viewer 啟動時若找不到 DB → 自動 `init_db()` 建立空 DB（不再退出） | `ctx-viewer.py: main` |
| **安全 S3-1** | `session_id`/`title` 前端改用 `dataset + addEventListener`，移除 onclick 字串組裝 | `ctx-viewer.py: HTML_PAGE` |
| **安全 S3-2** | 所有 POST/DELETE 檢查 `Origin`/`Referer` 是否為 `127.0.0.1`/`localhost` | `ctx-viewer.py: RequestHandler` |
| **安全 S3-3** | `OperationalError` 對外統一回 `"db busy or readonly"`（503），詳細錯誤只寫 log | `ctx-viewer.py` |
| **安全 S3-4** | POST body 大小限制 64KB，超過回 413 | `ctx-viewer.py` |
| **安全 S3-5** | `/api/search?q=` 關鍵字長度上限 200 字元，超過回 400 | `ctx-viewer.py` |
| **安全 S3-7** | `category` 白名單（`^[a-z0-9_-]{1,32}$`），不符入庫時改 `custom` | `ctx-db.py: save` |
| **安全 Session ID** | `session_id` 格式驗證（`^[A-Za-z0-9_-]{1,64}$`），不符 400 | `ctx-viewer.py` 路由層 |
| **安全 CSRF token** | `POST /api/saves/batch-delete` 需帶 `confirm_token`（HMAC-SHA256 of filter + started_at） | `ctx-viewer.py` |
| **效能 P3-1** | 單次 DELETE 拆分每批 5000 筆（batch delete） | `ctx-viewer.py: _handle_batch_delete` |
| **效能 P3-3** | `/api/sessions` 加 LIMIT 500 | `ctx-viewer.py` |
| **品質 Q3-4** | 前端 batch delete preview → confirm 之間比對 filter snapshot，不一致強制重新預覽 | `ctx-viewer.py: HTML_PAGE` |
| 環境變數 | 新增 `CTX_SAVE_DB_PATH`（覆寫 `~/.ctx-save/context.db`）、`CTX_SAVE_NO_MIGRATE`（跳過 auto-migrate） | `ctx-db.py` |
| `.gitignore` | Plugin 目錄加 `.ctx-save/`（避免 viewer.log、pid 等殘留誤 commit） | `plugins/ctx-save/.gitignore` |

### 1.2 Out of Scope

| 項目 | 備註 |
|---|---|
| DB 密碼保護、加密儲存 | 若未來需分享 DB 再另議 |
| FTS5 全文索引 | 先用 LIKE；集中後若資料 > 10 萬筆再評估 |
| 多使用者 shared DB（Cloud Sync） | 僅限單機集中 |
| 自動清除 legacy `.ctx-save/` 目錄 | 僅改名備份，刪除由使用者手動決定 |
| Rich 互動式 UI（React / Vue） | 維持原生 HTML/JS，減少依賴 |
| 多 DB 切換（Workspace 概念） | 一個 user 一個中央 DB |

---

## 2. API 設計

> 所有 API 端點由 `ctx-viewer.py` 的 `BaseHTTPRequestHandler` 提供，bind `127.0.0.1`，僅限本機存取。

### 2.1 API 總覽

| 端點 | Method | 用途 | 新/改 |
|---|---|---|---|
| `/api/ping` | GET | 健康檢查 + 服務識別 | 改（擴充欄位） |
| `/api/sessions` | GET | 列 session 清單 | 改（加 filter + LIMIT） |
| `/api/session/<id>` | GET | 取單一 session 的所有 records | 不變 |
| `/api/search` | GET | 關鍵字搜尋 | 改（加 filter + 長度限制） |
| `/api/stats` | GET | 統計資訊 | 改（加 by_project） |
| `/api/projects` | GET | 專案清單（供 sidebar 下拉） | **新** |
| `/api/saves/<id>` | DELETE | 刪除單筆 | 改（加 CSRF check） |
| `/api/session/<id>` | DELETE | 刪除整個 session | 改（加 CSRF check） |
| `/api/saves/batch-delete` | POST | 批次刪除（支援 dry_run） | 改（CSRF + 5000 分段 + 64KB + token） |

---

### 2.2 端點細節

#### GET `/api/ping`

| 項目 | 內容 |
|---|---|
| Query | 無 |
| Response 200 | `{"ok": true, "service": "ctx-viewer", "version": "2.1.0", "db_path": "/Users/xxx/.ctx-save/context.db", "project_count": 7, "record_count": 1523, "started_at": "2026-04-18T03:00:00Z"}` |
| Response 503 | `{"ok": false, "error": "db busy or readonly"}` |
| 備註 | `project_count = SELECT COUNT(DISTINCT project_path)`；launcher 用 `service=="ctx-viewer"` 判斷是否複用 |

#### GET `/api/sessions`

| 項目 | 內容 |
|---|---|
| Query | `project_path`（string, optional, 絕對路徑匹配）、`limit`（int, optional, 預設 500，最大 500） |
| Response 200 | `{"sessions": [{"session_id", "created_at", "project_path", "categories", "context_percentage", "item_count", "titles"}], "total_records": 1523}` |
| Response 400 | `q/limit` 非法：`{"ok": false, "error": "invalid limit"}` |
| SQL 範例 | `SELECT session_id, MIN(created_at), GROUP_CONCAT(DISTINCT category), MAX(context_percentage), COUNT(*), GROUP_CONCAT(title,' \| '), project_path FROM context_saves WHERE deleted_at IS NULL [AND project_path = ?] GROUP BY session_id, project_path ORDER BY MIN(created_at) DESC LIMIT 500` |

#### GET `/api/session/<id>`

| 項目 | 內容 |
|---|---|
| Path | `<id>` 須符合 `^[A-Za-z0-9_-]{1,64}$`，不符回 400 |
| Response 200 | `{"records": [{"id", "session_id", "project_path", "category", "title", "content", "tags", "context_percentage", "created_at", "model"}]}` |
| Response 400 | `{"ok": false, "error": "invalid session_id"}` |

#### GET `/api/search`

| 項目 | 內容 |
|---|---|
| Query | `q`（string, required, 長度 1–200）、`project_path`（string, optional） |
| Response 200 | `{"results": [...]}`（最多 50 筆） |
| Response 400 | `q` 超過 200 字元：`{"ok": false, "error": "q too long (max 200)"}`；`q` 為空：`{"ok": false, "error": "q required"}` |

#### GET `/api/stats`

| 項目 | 內容 |
|---|---|
| Query | 無 |
| Response 200 | `{"total_records", "total_sessions", "categories": {...}, "by_project": {"/Users/cheng/IdeaProjects/Taipei/LineBC": 312, ...}, "oldest", "newest"}` |

#### GET `/api/projects` **（新端點）**

| 項目 | 內容 |
|---|---|
| Query | 無 |
| Response 200 | `{"projects": [{"path": "/Users/cheng/IdeaProjects/Taipei/LineBC", "name": "LineBC", "record_count": 312, "session_count": 23, "last_at": "2026-04-18T01:30:00Z"}]}` |
| SQL | `SELECT project_path, COUNT(*) as records, COUNT(DISTINCT session_id) as sessions, MAX(created_at) as last_at FROM context_saves WHERE deleted_at IS NULL GROUP BY project_path ORDER BY last_at DESC` |
| `name` 邏輯 | 取 `Path(project_path).name`（basename），若相同則補 parent 一層 |

#### DELETE `/api/saves/<id>`

| 項目 | 內容 |
|---|---|
| Headers | `Origin` or `Referer` 必須為 `http://127.0.0.1:*` / `http://localhost:*` |
| Path | `<id>` 為整數，非法回 400 |
| Response 200 | `{"ok": true, "mode": "hard" \| "soft", "affected": 1, "id": 123}` |
| Response 400 | `{"ok": false, "error": "invalid id"}` |
| Response 403 | `{"ok": false, "error": "cross-origin request blocked"}` |
| Response 404 | `{"ok": false, "error": "record not found"}` |
| Response 503 | `{"ok": false, "error": "db busy or readonly"}` |

#### DELETE `/api/session/<id>`

| 項目 | 內容 |
|---|---|
| Headers | CSRF Origin check（同上） |
| Path | `<id>` 須符合 `^[A-Za-z0-9_-]{1,64}$` |
| Response 200 | `{"ok": true, "mode": "hard" \| "soft", "affected": N, "session_id": "..."}` |
| Response 400/403/404/503 | 同上 |

#### POST `/api/saves/batch-delete`

| 項目 | 內容 |
|---|---|
| Headers | `Content-Type: application/json`；`Origin`/`Referer` CSRF check；`Content-Length ≤ 65536`（64 KiB） |
| Request Body | `{"category": "task" \| null, "before": "ISO8601" \| null, "after": "ISO8601" \| null, "project_path": "..." \| null, "dry_run": bool, "confirm_token": "hex..." \| null}` |
| Token 規則 | `dry_run=false` 時必填；`HMAC-SHA256(key=STARTED_AT, msg=canonical_json(filter))`，server 端計算比對 |
| Dry run Response 200 | `{"ok": true, "dry_run": true, "affected": 42, "filter": {...}, "confirm_token": "hex..."}` |
| Confirm Response 200 | `{"ok": true, "dry_run": false, "mode": "hard" \| "soft", "affected": 42, "batches": 1, "filter": {...}}` |
| Response 400 | 無條件 / JSON 解析失敗 / token 不符：`{"ok": false, "error": "..."}` |
| Response 403 | CSRF 失敗：`{"ok": false, "error": "cross-origin request blocked"}` |
| Response 413 | Body > 64KB：`{"ok": false, "error": "payload too large"}` |
| Response 503 | DB busy/readonly |
| 執行邏輯 | 每批 5000 筆用 `DELETE FROM context_saves WHERE rowid IN (SELECT rowid FROM ... LIMIT 5000)`，迴圈直到 `rowcount == 0`；落回 soft delete 時改 `UPDATE SET deleted_at ...` 同樣分批 |

---

## 3. 業務邏輯規則

### 3.1 DB 位置決策

```
優先順序（高 → 低）：
1. 環境變數 CTX_SAVE_DB_PATH（絕對路徑；含 ~ 自動展開）
2. ~/.ctx-save/context.db（預設）
```

- `Path(os.environ.get("CTX_SAVE_DB_PATH", "~/.ctx-save/context.db")).expanduser().resolve()`
- 父目錄不存在時 `mkdir(parents=True, exist_ok=True)`

### 3.2 CLI 寫入（`ctx-db.py save`）

1. 取 `project_path = Path.cwd().resolve()`（可被 `CTX_PROJECT` 環境變數覆寫）
2. 驗證 `session_id` 格式（`^[A-Za-z0-9_-]{1,64}$`）— 不符拋 `ValueError`，CLI 印 JSON error 退出 2
3. 驗證 `category`：`re.match(r"^[a-z0-9_-]{1,32}$", c)`，不符則改為 `"custom"`（**寫入時強制，不拒絕**，保留原值於 `content` 前綴 `[原 category: xxx]\n`）
4. 開啟 DB（若不存在 → `init_db` + auto-migrate）
5. `INSERT INTO context_saves (session_id, project_path, ...) VALUES (...)`
6. 印 `{"status": "ok", "inserted": N, "db_path": "..."}`

### 3.3 Legacy 遷移（`ctx-db.py migrate-legacy`）

| 步驟 | 邏輯 |
|---|---|
| 1 | `Path.home().rglob(".ctx-save/context.db")` 掃描（排除 `~/.ctx-save/context.db` 本身） |
| 2 | 對每個 legacy DB：`ATTACH DATABASE 'legacy.db' AS src` |
| 3 | 以 `(session_id, created_at, title)` 為 natural key 去重：`INSERT INTO main.context_saves (...) SELECT ... FROM src.context_saves WHERE (session_id, created_at, title) NOT IN (SELECT session_id, created_at, title FROM main.context_saves)` |
| 4 | 若 legacy DB 無 `project_path` 欄位 → 用 legacy DB 所在父目錄的 parent（即 `{project}`）填入 |
| 5 | `DETACH DATABASE src`，改名 legacy 檔為 `context.db.migrated-YYYYMMDD` |
| 6 | 寫 `~/.ctx-save/.migration-done`（空檔，僅為旗標） |
| 7 | 印報表：`{"status": "ok", "migrated_files": N, "total_inserted": M, "skipped_duplicate": K, "renamed": [...]}` |
| 效能 | 10 萬筆合併 < 30 秒（在同顆 SSD 上 ATTACH + INSERT SELECT 批次操作） |

### 3.4 自動遷移觸發

- CLI 任何讀指令（`list` / `search` / `get` / `stats`）執行前檢查：
  - 若 `~/.ctx-save/.migration-done` 存在 → 跳過
  - 若 `CTX_SAVE_NO_MIGRATE=1` → 跳過
  - 否則執行一次 `migrate-legacy`，完成後列印 `ℹ 已遷移 N 筆舊紀錄到 ~/.ctx-save/context.db`

### 3.5 Session ID / Category 白名單

| 欄位 | 正則 | 不符時 |
|---|---|---|
| `session_id` | `^[A-Za-z0-9_-]{1,64}$` | CLI 拒絕（拋錯退出 2）；Viewer API 回 400 |
| `category`（寫入時） | `^[a-z0-9_-]{1,32}$` | 改為 `"custom"` |
| `category`（讀取時） | — | 原樣回傳（保留舊 PostgreSQL/MSSQL 風格值；不影響前端顯示） |

> **向後相容**：舊值例如 `postgresql-tuning`、`MSSQL_IndexDesign` 入庫雖被改為 `custom`，但若已在舊 DB 內存在，讀取時不改動。僅影響「新寫入」。

### 3.6 CSRF 防護

```
流程：
  1. POST/DELETE 請求到達
  2. 讀取 header: Origin（優先）、Referer（備援）
  3. 解析 hostname，允許 {"127.0.0.1", "localhost"}
  4. 不符合 → 403 {"error": "cross-origin request blocked"}
  5. 缺少 header 也視為非法 → 403
```

### 3.7 Confirm Token（batch-delete 二次確認）

```python
token = hmac.new(
    key=STARTED_AT.encode("utf-8"),
    msg=json.dumps(filter_dict, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    digestmod=hashlib.sha256,
).hexdigest()
```

- dry_run 回傳 `confirm_token`，前端 confirm 時原樣回傳
- server 重算並 `hmac.compare_digest` 比對，避免 timing attack
- filter 在 dry_run 與 confirm 之間若有差異 → token 算出來不同 → 400 `filter changed, please preview again`

### 3.8 前端 Filter Snapshot（Q3-4）

```javascript
// preview 後快照
let batchPreviewSnapshot = null;
async function previewBatchDelete() {
  const filter = collectBatchFilter();
  batchPreviewSnapshot = JSON.stringify(filter);
  // ... 呼叫 dry_run
  batchPreviewToken = data.confirm_token;
}

async function confirmBatchDelete() {
  const filter = collectBatchFilter();
  if (JSON.stringify(filter) !== batchPreviewSnapshot) {
    showToast('❌ 條件已變更，請重新預覽', 'error');
    document.getElementById('btnBatchConfirm').disabled = true;
    return;
  }
  // ... 呼叫 confirm 帶上 confirm_token
}
```

---

## 4. 錯誤處理策略

| 錯誤場景 | 偵測點 | HTTP / CLI 回應 | Log 內容 |
|---|---|---|---|
| CSRF 不通過 | RequestHandler 入口 | 403 `{"error": "cross-origin request blocked"}` | `WARN cross-origin from {origin}` |
| POST body > 64KB | RequestHandler 讀 `Content-Length` | 413 `{"error": "payload too large"}` | `WARN oversize body {n} bytes` |
| JSON 解析失敗 | `json.loads` | 400 `{"error": "invalid JSON"}` | 完整 exception |
| 搜尋 `q > 200` | GET `/api/search` | 400 `{"error": "q too long (max 200)"}` | — |
| `session_id` 非法 | Path 解析 | 400 `{"error": "invalid session_id"}` | — |
| `limit` 非整數 / 超範圍 | GET `/api/sessions` | 400 `{"error": "invalid limit"}` | — |
| `confirm_token` 不符 | batch-delete | 400 `{"error": "filter changed, please preview again"}` | `WARN token mismatch` |
| SQLite `OperationalError`（busy/readonly） | 所有 write 端（含 batch） | 503 `{"error": "db busy or readonly"}` | `ERROR OperationalError: {str(e)}` |
| 記錄不存在 | DELETE `affected == 0` | 404 `{"error": "record not found"}` | — |
| Viewer 啟動找不到 DB | `main()` | `init_db()` 自動建立空 DB，列印 `ℹ 建立空 DB: ~/...` | `INFO created empty db` |
| Legacy 遷移失敗（單檔） | `migrate-legacy` loop | 跳過該檔、記錄原因、繼續下一檔 | `WARN skip {path}: {err}` |
| CLI `session_id` 格式錯誤 | `save` stdin 驗證 | 退出 2，印 `{"error": "invalid session_id", "value": "..."}` | — |

---

## 5. 分層決策

| 層 | 路徑 | 職責 | 依據 |
|---|---|---|---|
| **CLI 層** | `ctx-save/SKILL.md` + Claude 呼叫 Bash | 使用者互動：分析對話、分類選擇、觸發寫入 | 維持既有 user-invocable skill，無架構變動 |
| **資料層** | `scripts/ctx-db.py` | DB 連線 / CRUD / schema 遷移 / legacy 合併 | 純 Python 模組化 function（`init_db`, `save`, `migrate_legacy`, `_connect_db`），方便被 `ctx-viewer.py` 以 `importlib` 動態載入 |
| **啟動層** | `ctx-view/scripts/ctx-view-launcher.py` | 定位 DB、stale PID 清理、port 探測、背景起 viewer、ping 驗證 | `ctx-view` skill 唯一進入點 |
| **Web 層** | `ctx-save/scripts/ctx-viewer.py` 的 `BaseHTTPRequestHandler` | HTTP 路由、參數驗證、CSRF、DB 讀寫、錯誤轉譯 | `http.server` + `ThreadingHTTPServer`，僅 bind 127.0.0.1 |
| **前端層** | `ctx-viewer.py: HTML_PAGE` 字串嵌入 | 使用者 UI、Session 列表、詳情、批次刪除、sidebar 過濾 | 原生 HTML/JS，純 vanilla（無 React/Vue） |

### 5.1 模組邊界規則

- `ctx-db.py` 不得 `import http.server`（資料層不碰 Web）
- `ctx-viewer.py` 不得直接組 SQL migration（改呼叫 `ctx-db.py` 的 `migrate_add_project_path` / `migrate_add_deleted_at`）
- `ctx-view-launcher.py` 不得直接讀 DB（委託 viewer 的 `/api/ping` 取 `db_path`）
- 前端 JS 不得 `eval()`、不得用 `innerHTML` 組 user-supplied 字串（全部走 `textContent` / `escapeHtml`）

---

## 6. 效能需求

| 項目 | 指標 | 實作手段 |
|---|---|---|
| Legacy 合併 10 萬筆 | < 30 秒 | `ATTACH DATABASE` + 單 `INSERT ... SELECT`（不走逐筆 Python loop） |
| `/api/sessions` 回應 | < 200ms（10 萬筆 DB） | LIMIT 500 + `idx_saves_created DESC` + `idx_saves_project` |
| `/api/search` 回應 | < 500ms | LIKE + LIMIT 50；超過 200 字元提前擋下 |
| batch-delete 1 萬筆 | < 3 秒 | 每批 5000 筆、`synchronous=NORMAL`、`journal_mode=WAL` |
| Viewer 啟動 → ping 成功 | < 3 秒 | `spawn + wait_for_ping(timeout=3)`（既有邏輯沿用） |
| 並發寫入不卡死 | 讀寫並發 OK | WAL + `busy_timeout=5000` + 寫端 `BEGIN IMMEDIATE` |

### 6.1 索引策略

```sql
CREATE INDEX IF NOT EXISTS idx_saves_session  ON context_saves(session_id);
CREATE INDEX IF NOT EXISTS idx_saves_project  ON context_saves(project_path);
CREATE INDEX IF NOT EXISTS idx_saves_created  ON context_saves(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saves_category ON context_saves(category);
CREATE INDEX IF NOT EXISTS idx_saves_deleted  ON context_saves(deleted_at);
-- 複合索引：sidebar 過濾「指定 project 最新 session」時加速
CREATE INDEX IF NOT EXISTS idx_saves_project_created
    ON context_saves(project_path, created_at DESC);
```

### 6.2 PRAGMA 設定

```python
# 開啟連線時統一套用
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA synchronous = NORMAL")
conn.execute("PRAGMA busy_timeout = 5000")
conn.execute("PRAGMA foreign_keys = ON")  # 預留
```

---

## 7. 向後相容性

### 7.1 既有用戶升級路徑

| 情境 | 行為 |
|---|---|
| 首次執行 `/ctx-save list`（或任一 read command） | 自動觸發 `migrate-legacy`，印提示一次；寫 `.migration-done` 旗標後不再觸發 |
| 既有 `/ctx-save` save 寫入 | 自動改寫入 `~/.ctx-save/context.db`；舊專案目錄 DB 不再累積新資料 |
| 既有 `.ctx-save/sessions/*.md`（Markdown 模式） | 不受影響，維持原行為 |
| 既有 port 29898 + PID file | 沿用；`CTX_VIEW_PORT` / `CTX_VIEW_NO_OPEN` 保留 |

### 7.2 新增環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `CTX_SAVE_DB_PATH` | `~/.ctx-save/context.db` | 覆寫中央 DB 路徑（給 CI / 測試用） |
| `CTX_SAVE_NO_MIGRATE` | unset | 設為 `1` 跳過 auto-migrate（給不想合併舊資料的使用者） |
| `CTX_VIEW_DB` | 跟隨 CTX_SAVE_DB_PATH | launcher 覆寫 viewer `--db` 參數 |
| `CTX_PROJECT` | `Path.cwd()` | CLI 寫入時的 project_path 覆寫（既有變數，保留） |

### 7.3 Schema 相容性

- 新舊 schema 差異僅「新增 `project_path` 欄位」
- `migrate_add_project_path` 冪等：先 `PRAGMA table_info` 檢查再 `ALTER TABLE`
- 舊資料 `project_path` 回填策略：遷移時用 legacy DB 所在路徑；`NULL` 或空字串在 viewer 顯示為 `"(unknown)"`

### 7.4 API 相容性

- 舊呼叫者（若有）呼叫 `/api/sessions` 不帶 `project_path` → 維持回傳全部
- `/api/ping` 新增欄位不破壞舊欄位（`service` / `version` 仍存在）
- 回傳格式維持 JSON；新增欄位 additive

---

## 8. 成功準則對應表

| 驗收項 | 對應 spec 章節 | 驗證方式 |
|---|---|---|
| 多專案資料都進中央 DB | §3.1、§3.2 | 從 2 個不同 cwd 執行 `/ctx-save`，SQL `SELECT DISTINCT project_path` 應有 2 筆 |
| Viewer header 顯示 DB 路徑 + 版本 | F-6 | 瀏覽器打開 viewer，肉眼確認 |
| Sidebar 專案下拉 | F-7、§2.2 `/api/projects` | 切換下拉，sessions 列表即時過濾 |
| Legacy 遷移腳本 | §3.3 | 人工建 `{temp}/.ctx-save/context.db`，執行 `migrate-legacy` 後中央 DB 有資料、舊檔改名 |
| CSRF 403 | §3.6、§2.2 | `curl -H 'Origin: http://evil.com' -X POST .../batch-delete` → 403 |
| XSS 不執行 | F-S3-1、F-S3-7 | 手塞 `session_id = "abc');alert(1);//"` 後打開 viewer，無 alert |

---

## 9. 測試計畫（供 build/verify 階段參考）

### 9.1 單元測試（pytest / 標準庫 `unittest`）

- `test_ctx_db_save_with_project_path` — `save()` 正確寫入 `project_path`
- `test_ctx_db_category_whitelist` — 非法 category 被改為 `custom`
- `test_ctx_db_session_id_validation` — 非法 session_id 拋錯
- `test_migrate_add_project_path_idempotent` — 連續呼叫 2 次不出錯
- `test_migrate_legacy_dedup` — 重複 `(session_id, created_at, title)` 不重插
- `test_migrate_legacy_rename` — 舊檔改名為 `.migrated-YYYYMMDD`

### 9.2 整合測試（啟動真 viewer）

- `test_api_ping_fields` — 回傳含 `service/version/db_path/project_count/record_count`
- `test_api_sessions_filter` — `?project_path=xxx` 只回該專案
- `test_api_projects` — 新端點結構正確
- `test_api_csrf_block` — 無 Origin 或 cross-origin → 403
- `test_api_body_too_large` — 70KB body → 413
- `test_api_q_too_long` — `q` 201 字元 → 400
- `test_batch_delete_token_roundtrip` — dry_run → token → confirm 成功
- `test_batch_delete_token_filter_changed` — dry_run 後改 filter → 400
- `test_batch_delete_5000_chunks` — 插 12000 筆 → batch delete → 回 `batches: 3`

### 9.3 端對端（手動）

- 從 `Taipei/LineBC` 和 `Fubon-LineBC` 兩個 cwd 各存一筆 → viewer sidebar 顯示兩專案
- 執行 `migrate-legacy`，確認舊 `.ctx-save/context.db` 改名

---

## 10. 變更檔案清單（實作階段對照用）

| 檔案 | 變動類型 | 主要改動 |
|---|---|---|
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py` | 修改 | `get_db_path` 改集中化、`migrate_add_project_path`、`migrate_legacy`、category 白名單、`_connect_db(writer)` |
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-viewer.py` | 修改 | 新 API（`/api/projects`）、CSRF、body 限制、confirm_token、header UI、sidebar 下拉、LIMIT 500、batch 分段 |
| `plugins/ctx-save/skills/ctx-view/scripts/ctx-view-launcher.py` | 修改 | stale PID 清理強化、`CTX_VIEW_DB` 支援、`find_db` 改集中 |
| `plugins/ctx-save/skills/ctx-save/SKILL.md` | 修改 | 更新儲存路徑說明、新增 `migrate-legacy` 指令、環境變數列表 |
| `plugins/ctx-save/.gitignore` | 新增 | `.ctx-save/` |
| `plugins/ctx-save/skills/ctx-save/references/storage-schema.md` | 修改 | Schema 新增 `project_path` 欄位說明 |

---

## 判斷
- FRONTEND_REQUIRED: true
- FRONTEND_TECH: 原生 HTML/JS（嵌入 Python http.server）
- DB_REQUIRED: true
- DB_TABLES: `context_saves` 修改（加 project_path 欄位與索引），無新表
