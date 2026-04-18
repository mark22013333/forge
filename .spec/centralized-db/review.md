# 程式碼審查報告

## 審查日期
2026-04-18

## 審查範圍
3 個 Python 檔案（共 3655 行）

- `plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py`（969 行）
- `plugins/ctx-save/skills/ctx-save/scripts/ctx-viewer.py`（2078 行，內含 ~1262 行 HTML/JS/CSS）
- `plugins/ctx-save/skills/ctx-view/scripts/ctx-view-launcher.py`（608 行）

## 審查模式

3 位並行 Subagent（非 Agent Teams，檔案互不重疊）：

| Reviewer | 模型 | 專職 |
|----------|------|------|
| Logic Reviewer | Opus | 邏輯正確性（對 spec / arch 契約） |
| Quality Reviewer | Sonnet | 風格一致性、可維護性 |
| Security Reviewer | Opus | CSRF / XSS / SQLi / InfoDisc / 效能 |

## 統計

| 類別 | 🔴 嚴重 | 🟡 建議 | 🟢 良好 |
|------|---------|---------|---------|
| 邏輯正確性 | 4 | 15 | 8 |
| 程式碼品質 | — | 17 | 10 |
| 安全 | 0（critical/high）| 3（medium）/ 2（low） | 9 |
| 效能 | — | 3（medium）/ 4（low） | — |
| **合計** | **4** | **44** | **27** |

> 備註：Security 視角自評「可結案」（因 3 個 medium 皆為本機工具邊界情形）；Logic 視角自評「不可結案」（4 個嚴重裡有 L-13、L-16 影響資料正確性 / launcher 啟動判斷）。綜合建議：**先修 L-1/L-13/L-16 後再 close**。

---

## 🔴 嚴重問題（必修）

### [L-1] `isolation_level=None` + batch delete 多輪 commit 後，下一輪無 `BEGIN IMMEDIATE` → 失去交易保護

- **檔案**：`ctx-viewer.py:1937-1987`（`_delete_with_fallback`）；`ctx-db.py:107, 535-574, 744-752, 889-892`
- **Reviewer**：logic（+ quality Q-12、Q-17 同源）
- **問題**：
  - `_connect_db()` 用 `isolation_level=None`（autocommit），`with conn:` 只在例外時 rollback，**不會自動 COMMIT**。
  - `writer=True` 會執行 `BEGIN IMMEDIATE`，之後每輪 `conn.commit()` 關閉交易。**第二輪起再執行 `DELETE` 時，已回到 autocommit 狀態，每行 DELETE 各自一交易**，失去批次鎖保護。
  - 更嚴重：若 hard-delete 中途失敗落到 fallback soft-delete（`conn.rollback()`），在 autocommit 下是 no-op，**已 commit 的部分無法復原**。
- **建議**：
  1. 每輪 COMMIT 後，在下一輪 DELETE 前明確 `conn.execute("BEGIN IMMEDIATE")`；或
  2. 移除 `isolation_level=None`，改回預設隱式交易 + `with conn:` 自動管理；或
  3. 統一使用 `conn.execute("COMMIT")` / `conn.execute("ROLLBACK")`，與 ctx-db.py 風格對齊。

### [L-3] `maybe_auto_migrate()` 只跑 legacy 合併，**不跑 schema migration** → 外部指定 v1 舊 DB 直接噴 OperationalError

- **檔案**：`ctx-db.py:493-514`、`ctx-viewer.py:2044-2048`
- **Reviewer**：logic
- **問題**：
  - `maybe_auto_migrate()` 只呼叫 `migrate_legacy`（合併舊 per-project DB），**未呼叫 `_run_all_migrations`**。
  - 若使用者 `CTX_SAVE_DB_PATH=/old/v1/db.sqlite` 指向一個沒有 `deleted_at` / `project_path` 欄位的舊 DB，`list_sessions` 的 `WHERE deleted_at IS NULL` 直接噴 `no such column`。
  - viewer `main()` 有呼叫 `_run_all_migrations`，但 launcher 在 DB 已存在時（`ctx-view-launcher.py:216-217`）直接 return，**不觸發任何 migration**。
- **建議**：
  1. `maybe_auto_migrate()` 前置呼叫 `_run_all_migrations(central_db)`（冪等）；
  2. launcher 的 `find_db()` 於確認 DB 存在後，透過 importlib 呼叫 `ctx_db._run_all_migrations`，migration 失敗則拒絕啟動。

### [L-13] `/api/ping` 在 DB busy 時回 **HTTP 200** + `ok:false`，違反 spec §2.2 要求 **HTTP 503**

- **檔案**：`ctx-viewer.py:1594-1606`
- **Reviewer**：logic
- **問題**：launcher 的 `wait_for_ping` 以 HTTP 200 判定啟動成功。此實作讓 DB busy 時 ping 回 200，launcher 誤判啟動完成，之後使用者瀏覽 UI 才看到 API 503 錯誤。
- **建議**：改為 `self._json_response(503, {"ok": false, "error": "db busy or readonly"})`，並在 launcher 端將 503 視為「啟動中，繼續等」而非「啟動失敗」。

### [L-16] 前端 batch-delete `before/after` 送 `YYYY-MM-DDT00:00:00Z` 與 SQLite `CURRENT_TIMESTAMP`（`YYYY-MM-DD HH:MM:SS`）字典序**不相容** → 資料範圍誤判

- **檔案**：`ctx-viewer.py:1076-1077`（前端 `collectBatchFilter`）
- **Reviewer**：logic
- **問題**：
  - SQLite 預設 `CURRENT_TIMESTAMP` 格式無 `T` 無 `Z`，空格分隔。
  - 前端送 `'2026-01-01T00:00:00Z'`，字典序 `'T' > ' '`、`'Z' > '9'`，導致 `created_at < '2026-01-01T00:00:00Z'` **會誤刪 `2026-01-01 23:59:59` 的資料**。
  - 這是資料正確性問題，**batch delete 範圍選擇錯誤**。
- **建議**：
  - 前端 normalize 為 `${beforeDate} 00:00:00` / `${afterDate} 23:59:59`；或
  - 後端 `_handle_batch_delete` 檢測 ISO 格式並轉為 SQLite 原生格式；
  - 同時新增 regex 驗證 `^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z?)?$` 防畸形輸入（補 L-5）。

---

## 🟡 改善建議（選擇性 / 下一輪）

### 邏輯相關

- **[L-2]** `save()` 對 `title`、`content` 空字串通過 NOT NULL，應在寫入前 reject 空值（`ctx-db.py:555-572`）。
- **[L-4]** spec.md §3.7 與 arch.md §6.2 的 HMAC 設計不一致（spec 說 STARTED_AT as key，arch 說 CONFIRM_SECRET as key + STARTED_AT as salt）。**實作採 arch 版本，較安全**。建議回頭修 spec.md 對齊。
- **[L-5]** batch filter `before/after` 除長度外無格式驗證，容易被亂字串當 SQL string 比較（`ctx-viewer.py:1848-1851`）。
- **[L-6]** `_delete_with_fallback` 的 `n < batch_size` 提前 break 在併發寫入下會漏刪少量剛進來資料（規格允許，補 comment）。
- **[L-7]** `GROUP_CONCAT(title, ' | ')` 無長度限制，大 session 回應可能超長（`ctx-viewer.py:1633`）。
- **[L-15]** `_scan_legacy_dbs` 實作偏離 spec §3.3（spec 要求 `Path.home().rglob`，實作改為白名單目錄 + env 覆寫）。**必要偏離**（避免 home-wide rglob hang），但需回頭補 spec 文件。
- **[L-18]** `get_db()` 的 row_factory fallback 註解誤導（`ctx-viewer.py:1488-1492`）。
- **[L-19]** launcher DB 存在時不跑 migration，與 viewer migration swallow exception 組合會讓壞 DB 也能啟動（與 L-3 同類）。

### 品質相關（17 項，重點列 6 項）

- **[Q-1]** `list_projects` 回傳值 vs 其他 CLI 函式直接 print stdout 介面不一致（ctx-db.py）。建議統一為「回傳值 + CLI wrapper 負責 print」，提升可測試性。
- **[Q-6]** `_delete_with_fallback` 的 hard-delete / soft-delete 批次迴圈重複 40+ 行，應抽 `_run_batch_sql(conn, sql, args, batch_size)`。
- **[Q-7]** `spawn_viewer` 中 `log_file = open(...)` 若 `Popen` 失敗，fd 洩漏（`ctx-view-launcher.py:392-409`）。改用 `with open(...)` 包裝。
- **[Q-8]** `LIMIT 50` 在 ctx-db / ctx-viewer 兩處 magic number，應抽常數 `SEARCH_RESULTS_LIMIT`。
- **[Q-11]** `CTX_DB = _load_ctx_db()` 在 module top-level 執行副作用，影響 import / mock。建議延遲到 `main()`。
- **[Q-12]** `save()` 中 `conn.execute("ROLLBACK")` + `raise` 的行為在 `isolation_level=None` 下需驗證（與 L-1 同源）。

其餘 11 項（Q-2 / Q-3 / Q-4 / Q-5 / Q-9 / Q-10 / Q-13 / Q-14 / Q-15 / Q-16 / Q-17）見各 Reviewer 完整報告（彙整於本 file 末尾附錄）。

### 安全（medium 3 項）

- **[S-1]** CSRF 白名單 `{"127.0.0.1", "localhost"}` 不檢查 port，**同機任意 127.0.0.1:xxxx 的服務都能打** `/api/saves/batch-delete`。建議檢查完整 `Origin` 字串或加入 port 限制，或在文件警示「同機工具信任模型」。
- **[S-2]** `Content-Length` 不驗 `Transfer-Encoding`。若攻擊者送 chunked encoding，`rfile.read(CL)` 只讀 CL bytes，殘留資料污染 keep-alive 下一請求（request smuggling 風險）。建議拒絕 `Transfer-Encoding` 或 `_read_json_body` 結尾強制 `self.close_connection = True`。
- **[S-3]** `CTX_SAVE_MIGRATE_ROOTS` 可注入任意路徑讓 `ATTACH DATABASE` 讀取，若攻擊者能控制環境變數可污染中央 DB。建議限制在 `Path.home()` 子目錄。

### 效能（medium 3 項）

- **[P-2]** Legacy migration 在單一大交易中 ATTACH+INSERT N 個 legacy DB，WAL 可能累積 >100MB。建議每個 legacy DB 獨立交易。
- **[P-3]** `_delete_with_fallback` 的 `DELETE ... WHERE id IN (SELECT id ... LIMIT 5000)` 每批重新掃 filter。建議改為 `WHERE id > :last_id` 游標模式或 `WHERE rowid IN (...)`。
- **[P-4]** `list_projects` 無 `LIMIT`，使用者有 1000+ 專案時 sidebar 渲染爆掉。建議 `LIMIT 200`。

---

## 🟢 良好實踐（27 項，摘要）

- HMAC 使用 `secrets.token_bytes(32)` + `compare_digest` + `STARTED_AT` salt，避免 timing attack 與跨啟動重放
- 前端全面 `textContent` / `dataset` / `addEventListener`，無 `onclick` 字串拼接（S3-1/S3-7 修復確認）
- `_wrap_operational_error` 統一回 503 不洩漏原始訊息（S3-3 修復確認）
- Migration 冪等設計（`CREATE INDEX IF NOT EXISTS` + `PRAGMA table_info` 偵測）支援漸進升級
- PID file 使用 `write + os.replace` 原子替換
- `try_reuse_existing` 比對 PID file DB 路徑，避免切換 DB 後誤重用舊 viewer
- `_scan_legacy_dbs` 排除中央 DB 自身與 `~/.ctx-save/`，防自我吸收
- Legacy 合併的自然 key 去重（session_id + created_at + title）
- ATTACH → INSERT → DETACH → rename-after-commit 流程嚴謹，避免 SQLite 檔鎖衝突
- Guard clause 風格一致，嵌套層級 ≤3
- 無裸 `except: pass` 吞噬業務例外
- 模組與函式 docstring 完整
- Type hints 覆蓋率高（Path / Optional / List / Dict / Tuple）

---

## 交叉審查發現

### 1. `isolation_level=None` 交易管理是**跨 reviewer 共識的系統性問題**

- **Logic L-1**、**Quality Q-12 / Q-17** 各自從不同角度發現同一根源：`isolation_level=None` 下 Python `with conn:` 不自動 commit / rollback，手動 `ROLLBACK` / `commit` 的行為與 autocommit 模式互動不清。
- 建議整體重新審視 ctx-db / ctx-viewer 的交易邊界，統一為下列兩種策略之一：
  - **Option A**：全面 `isolation_level=None`，每段交易顯式 `BEGIN IMMEDIATE` + `COMMIT` / `ROLLBACK`，不依賴 `with conn:`。
  - **Option B**：移除 `isolation_level=None`，改回預設隱式交易，`with conn:` 自動處理 commit/rollback；在需要 immediate lock 時手動 `BEGIN IMMEDIATE`。
- **建議採 Option A**（當前實作方向），但需補齊 batch delete 的每輪 BEGIN、移除多餘的 `conn.rollback()` no-op 呼叫。

### 2. L-13（ping 回碼）與 Security S3-3（OperationalError 不洩漏）同源

- Security 確認 S3-3 已修（OperationalError 轉 503），但 `/api/ping` 特殊走 200 OK=false 路線違反規格。建議 ping 也遵循 503 慣例，launcher 端改寫 `wait_for_ping` 接受 503「啟動中」狀態。

### 3. L-16（日期格式）Security 與 Quality **皆未發現**

- 此為 Logic reviewer 獨有的深度發現。前端送 `2026-01-01T00:00:00Z`，SQLite 存 `2026-01-01 23:59:59`，字典序比較導致**刪多**。
- Security 的對抗性三角色檢查聚焦於注入與繞過，未觸及「合法輸入在業務邏輯下被誤判」的 case。
- 建議日期處理加 unit test，模擬「一天界線」場景。

### 4. Security 的「可結案」與 Logic 的「不可結案」如何調解

- Security 結論：本機工具的 3 個 medium 不構成外網攻擊面 → 可接受。
- Logic 結論：資料正確性（L-16）、啟動判斷（L-13）、交易保護（L-1）、schema migration（L-3）是正確性層次問題，應修。
- **綜合建議**：
  - **必修**：L-1（交易）、L-3（migration）、L-13（ping）、L-16（日期）
  - **可選**：其他建議與 medium 安全 / 效能
  - **不阻塞**：Round-3 九項漏洞修復全數確認

---

## 結論

- **Round-3 漏洞修復**：9/9 全數通過驗證（S3-1~S3-7 + P3-1 + P3-3 + Q3-4）
- **本輪新發現嚴重問題**：4 項（L-1 / L-3 / L-13 / L-16）— 建議 hotfix 後再 `/plan-close`
- **非阻塞改善**：44 項（建議 15 邏輯 + 17 品質 + 3+4 安全/效能）
- **整體評價**：架構設計正確、Round-3 漏洞全修；主要風險集中在 SQLite 交易邊界與日期格式這類**容易忽略的正確性細節**
- **下一步建議**：
  1. 修 4 項嚴重問題（預估 < 1 天）
  2. 再跑一次 smoke test + E2E（`/plan-verify`）
  3. 確認無迴歸後 `/plan-close`

---

## 附錄：Quality Reviewer 完整建議清單（未列於上方者）

- **[Q-2]** `_main` 中 `list` / `clean` 指令 `int(argv[2])` 無 try/except，ValueError 噴 traceback（ctx-db.py:917, 956）
- **[Q-3]** `migrate_legacy` dry-run 分支用裸 `sqlite3.connect` 而非 `_connect_db`
- **[Q-4]** `DB_PATH` 全域可變狀態無 `Final` 標注
- **[Q-5]** `_handle_sessions` / `_handle_search` 使用 f-string 拼 `where_sql`，與 ctx-db 的字串串接風格不一致（雖實質安全）
- **[Q-9]** `_homeify`（launcher） / `_home_relative`（viewer）邏輯重複，因 importlib 隔離無法共用，需文件標註
- **[Q-10]** `_home_relative` 缺 type hint
- **[Q-13]** `stats()` 6 個 SELECT 無顯式 `BEGIN DEFERRED` 保護
- **[Q-14]** 前端 `window._searchTimer` 應改 let 宣告於模組作用域；debounce `200ms` 應抽常數
- **[Q-15]** `_cleanup_stale_pid` 缺 `-> None` 標注
- **[Q-16]** `_kill_child` 的 `range(5)` / `time.sleep(0.1)` 應與 `SPAWN_POLL_INTERVAL_SEC` 統一管理

## 附錄：Logic Reviewer 完整建議清單（未列於上方者）

- **[L-8]** `_handle_session_detail` / `_handle_search` 的 OperationalError 處理鏈路正確，建議補 unit test
- **[L-9]** `try_reuse_existing` 的 `Path.resolve()` 比對正確，防 None 亦有
- **[L-10]** `find_db()` 重複檢查可簡化
- **[L-11]** `maybe_auto_migrate` 的 `.migration-done` flag 行為正確
- **[L-12]** `collectBatchFilter` 跳過空值欄位，前端比對正確
- **[L-14]** 單一 delete 的 `commit()` + rowcount=0 後無 ROLLBACK，實務無副作用但不潔
- **[L-17]** `_single_delete_with_fallback` 的 hard→soft fallback 情境罕見
