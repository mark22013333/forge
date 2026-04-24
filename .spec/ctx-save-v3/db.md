# ctx-save v3 資料庫設計

> **技術棧**：SQLite 3（Python 標準庫 `sqlite3`）
> **DB 路徑**：`~/.ctx-save/context.db`（WAL mode，v2.3 既有）
> **上游文件**：[spec.md §4 資料模型變更](./spec.md#4-資料模型變更)
> **撰寫日期**：2026-04-21

---

## 1. 設計原則

v3 是 v2.3 的**增量演進**，不重建 DB：

1. **沿用既有 connection / PRAGMA / WAL 設定**（`ctx-db.py::_connect_db`）
2. **所有 migration 冪等**：讀 `PRAGMA table_info()` 或 `sqlite_master` 判斷欄位/表是否存在
3. **Migration flag 換代**：`~/.ctx-save/.migration-done` → `~/.ctx-save/.migration-v3-done`（v2 舊 flag 保留，重跑不會出錯）
4. **新欄位全部 NULL-tolerant 或帶 DEFAULT**：避免 `ALTER ADD NOT NULL` 在 SQLite 的限制
5. **命名慣例**：表名小寫複數、欄位小寫底線、索引 `idx_{table}_{column}`（對齊 `.claude/rules/database.md`）
6. **軟刪除**：沿用 `deleted_at DATETIME` 模式（所有新表一致）

---

## 2. v2.3 既有 Schema（作為基線）

```sql
CREATE TABLE context_saves (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    project_path       TEXT NOT NULL DEFAULT '(unknown)',
    context_percentage REAL,
    title              TEXT NOT NULL,
    category           TEXT NOT NULL,
    content            TEXT NOT NULL,
    tags               TEXT DEFAULT '',
    model              TEXT DEFAULT '',
    deleted_at         DATETIME
);
-- 索引
idx_saves_session          (session_id)
idx_saves_project          (project_path)
idx_saves_created          (created_at DESC)
idx_saves_category         (category)
idx_saves_deleted          (deleted_at)
idx_saves_project_created  (project_path, created_at DESC)
```

**既有 migrations**：
- `migrate_add_deleted_at` — 補 `deleted_at` 欄位與索引
- `migrate_add_project_path` — 補 `project_path` 欄位、預設 `'(unknown)'`、補兩個索引

---

## 3. v3 Schema 變更總覽

| 項目 | 動作 | 里程碑 |
|------|------|--------|
| `context_saves.is_persistent` | 新增欄位 INTEGER DEFAULT 0 | M4（N4 Persistent Notes） |
| `context_saves.restored_at` | 新增欄位 DATETIME | M1（N1 自動還原） |
| `context_saves.distilled_from_id` | 新增欄位 INTEGER | M4（N3 Distill） |
| `context_diagnoses` | 新表 | M2（N2 analyze） |
| `context_compact_events` | 新表（選做） | M4（效能需要才建） |
| `idx_saves_persistent` | 新索引 | M4 |
| `idx_saves_restored` | 新索引 | M1 |
| `idx_saves_distilled` | 新索引 | M4 |
| `idx_diag_session` / `idx_diag_ran` | 新索引 | M2 |

---

## 4. `context_saves` 擴充

### 4.1 新增欄位

| 欄位 | 型別 | 預設 | NULL | 用途 |
|------|------|------|------|------|
| `is_persistent` | INTEGER | 0 | NOT NULL | 長效筆記旗標。N4 `/ctx-save pin` 會把筆記設為 1；`clean` 子指令會跳過 `is_persistent=1` 的筆記 |
| `restored_at` | DATETIME | NULL | NULL | 最近一次被 auto-reinject 的時間。`ctx-reinject.py` 注入成功後回寫，避免同一筆在多個連續 compact 內反覆重注 |
| `distilled_from_id` | INTEGER | NULL | NULL | 若此筆由 N3 distill 產生，指向原始 `context_saves.id`。Web Viewer 用此關聯顯示「瘦身版 / 原始版」切換 |

**為什麼 `distilled_from_id` 不建外鍵**：
SQLite 預設 `PRAGMA foreign_keys=OFF`。保留鬆耦合以容忍原 snapshot 被 `clean` 刪除（distilled 版本應能獨立存活）。改為應用層在 JOIN 時 LEFT JOIN 處理「原始已刪」情境。

### 4.2 新增索引

| 索引 | 欄位 | 用途 |
|------|------|------|
| `idx_saves_persistent` | `is_persistent` | `WHERE is_persistent=1` 查長效筆記（Persistent tab） |
| `idx_saves_restored` | `restored_at DESC` | `ctx-reinject.py` 找「最近一筆尚未 restored 的 auto-dump」 |
| `idx_saves_distilled` | `distilled_from_id` | Web Viewer 從原始 id 找 distill 衍生鏈 |

### 4.3 DML 變更點

`clean` 指令（保留近 N 天）SQL 從：

```sql
-- v2.3
DELETE FROM context_saves
WHERE created_at < ?
  AND deleted_at IS NULL;
```

改為：

```sql
-- v3
DELETE FROM context_saves
WHERE created_at < ?
  AND deleted_at IS NULL
  AND is_persistent = 0          -- 新：保護長效筆記
  AND (restored_at IS NULL
       OR restored_at < ?);      -- 新：保護剛還原過的 snapshot（7 天內）
```

第二個 `?` 為 `now - restore_protect_days`，預設 7 天。

---

## 5. 新表 `context_diagnoses`

### 5.1 結構

```sql
CREATE TABLE IF NOT EXISTS context_diagnoses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT NOT NULL,
    project_path         TEXT NOT NULL DEFAULT '(unknown)',
    ran_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
    context_percentage   REAL,
    score                INTEGER,
    tokens_conversation  INTEGER,
    tokens_files         INTEGER,
    tokens_tools_schema  INTEGER,
    tokens_tools_runtime INTEGER,
    tokens_system        INTEGER,
    findings_json        TEXT NOT NULL,
    suggest_focus        TEXT,
    deleted_at           DATETIME
);
```

| 欄位 | 型別 | NULL | 說明 |
|------|------|------|------|
| `id` | INTEGER PK | NOT NULL | 自增 |
| `session_id` | TEXT | NOT NULL | 對應 Claude Code session |
| `project_path` | TEXT | NOT NULL, DEFAULT `'(unknown)'` | 與 `context_saves` 一致 |
| `ran_at` | DATETIME | DEFAULT `CURRENT_TIMESTAMP` | analyze 執行時間 |
| `context_percentage` | REAL | NULL | 跑 analyze 當下的使用率（來自 `statusline-last-input.json`） |
| `score` | INTEGER | NULL | 0-100 健康分（§6.5 公式） |
| `tokens_conversation` | INTEGER | NULL | 五類 token 分類（spec §6.3） |
| `tokens_files` | INTEGER | NULL | 檔案讀取 |
| `tokens_tools_schema` | INTEGER | NULL | 啟動載入的 tool definitions |
| `tokens_tools_runtime` | INTEGER | NULL | Bash/WebFetch/MCP 呼叫與結果 |
| `tokens_system` | INTEGER | NULL | 系統提示（CLAUDE.md 等） |
| `findings_json` | TEXT | NOT NULL | `json.dumps` 後的 findings list |
| `suggest_focus` | TEXT | NULL | `/compact focus on X,Y` 中的 X,Y 字串 |
| `deleted_at` | DATETIME | NULL | 軟刪除 |

### 5.2 索引

| 索引 | 欄位 | 用途 |
|------|------|------|
| `idx_diag_session` | `session_id` | Web Viewer 依 session 列歷次診斷 |
| `idx_diag_ran` | `ran_at DESC` | Dashboard tab「最新健康報告」 |
| `idx_diag_project_ran` | `project_path, ran_at DESC` | 依 project 看趨勢 |
| `idx_diag_deleted` | `deleted_at` | 軟刪除過濾 |

### 5.3 為什麼獨立成表而不是加欄位到 `context_saves`

- **量級不同**：analyze 可一天跑數次，findings_json 常破 10KB；放 `context_saves` 會讓既有查詢讀到不必要的大欄位
- **語意不同**：`context_saves` 是「使用者/系統主動存的快照」，`context_diagnoses` 是「被動產生的診斷結果」
- **category 欄位維持乾淨**：避免加入 `'diagnosis'` 污染既有 10 種 category 白名單

### 5.4 `findings_json` Schema（應用層約定）

```json
[
  {
    "type": "duplicate_read",
    "severity": "medium",
    "file": "PushService.java",
    "count": 6,
    "messages": [3, 45, 67, 89, 112, 134],
    "estimated_tokens": 2100,
    "root_cause_key": "duplicate_read:PushService.java"
  },
  {
    "type": "unused_mcp_server",
    "severity": "low",
    "server": "mcp__linear",
    "schema_tokens": 3400,
    "invocations": 0,
    "root_cause_key": "unused_mcp:linear"
  }
]
```

`root_cause_key` 用於 §6.4 Layer 3 根因去重。

---

## 6. 新表 `context_compact_events`（M4 選做）

### 6.1 結構

```sql
CREATE TABLE IF NOT EXISTS context_compact_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    project_path TEXT NOT NULL DEFAULT '(unknown)',
    happened_at  DATETIME NOT NULL,
    trigger      TEXT,
    context_pct  REAL,
    snapshot_id  INTEGER,
    reinjected   INTEGER NOT NULL DEFAULT 0,
    deleted_at   DATETIME
);
```

| 欄位 | 型別 | NULL | 說明 |
|------|------|------|------|
| `trigger` | TEXT | NULL | `auto` / `manual`（Claude Code PreCompact hook 會帶 trigger 欄位） |
| `snapshot_id` | INTEGER | NULL | 指向對應 `context_saves.id`（原始 auto-dump）。鬆耦合，不建 FK |
| `reinjected` | INTEGER | NOT NULL DEFAULT 0 | 0/1；`ctx-reinject.py` 完成後改 1 |

### 6.2 索引

| 索引 | 欄位 | 用途 |
|------|------|------|
| `idx_compact_happened` | `happened_at DESC` | N6 Timeline 視圖的主排序 |
| `idx_compact_session` | `session_id` | 單 session 事件軸 |
| `idx_compact_reinjected` | `reinjected` | 找「未還原」事件做離線重試（M4 之後才可能需要） |

### 6.3 為什麼獨立成表

- N6 Web Timeline 要快速拉出「最近 100 次 compact」，用 `WHERE category='auto-dump'` 查 `context_saves` 會掃到大量內容欄位
- 這張表是**索引用途**而非內容儲存，content 在 `context_saves.id = snapshot_id` 裡
- **M3 之前可用 query view 頂著**（例如 `SELECT id, created_at, context_percentage FROM context_saves WHERE category='auto-dump'`），M4 視效能決定是否實際建表

### 6.4 寫入時機

| Hook | 動作 |
|------|------|
| PreCompact (`ctx-autodump.py`) | 寫 `context_saves` 後 `INSERT INTO context_compact_events (session_id, happened_at, trigger, context_pct, snapshot_id, reinjected=0)` |
| SessionStart matcher=compact (`ctx-reinject.py`) | 成功注入後 `UPDATE context_compact_events SET reinjected=1 WHERE id=?` **並** `UPDATE context_saves SET restored_at=CURRENT_TIMESTAMP WHERE id=snapshot_id` |

兩個 UPDATE 必須同一 transaction。

---

## 7. Migration 流程

沿用 v2.3 的 `_run_all_migrations()` 架構，新增函式：

```python
def migrate_add_persistent(conn) -> bool: ...
def migrate_add_restored_at(conn) -> bool: ...
def migrate_add_distilled_from(conn) -> bool: ...
def migrate_create_diagnoses_table(conn) -> bool: ...
def migrate_create_compact_events_table(conn) -> bool: ...  # M4 再啟用
```

### 7.1 執行順序

```
_init_schema(conn)            # 對新 DB 直接建全版 schema（含 v3 欄位）
  ↓
_run_all_migrations(conn):
  1. migrate_add_deleted_at         (v2 遺留，對已完成者直接回 False)
  2. migrate_add_project_path       (v2 遺留)
  3. migrate_add_persistent         (v3 新)
  4. migrate_add_restored_at        (v3 新)
  5. migrate_add_distilled_from     (v3 新)
  6. migrate_create_diagnoses_table (v3 新)
  7. migrate_create_compact_events_table (v3 新，M4)
  ↓
touch ~/.ctx-save/.migration-v3-done
```

### 7.2 對新 DB 的特化

`_init_schema` 要同時建出 v3 全欄位的 `context_saves`，避免「新 DB → 跑 migration → 徒增 ALTER 呼叫」。v3 `_init_schema` 的 CREATE TABLE 直接包含：

```sql
is_persistent      INTEGER NOT NULL DEFAULT 0,
restored_at        DATETIME,
distilled_from_id  INTEGER,
```

migrate 函式的 `PRAGMA table_info()` 偵測會讓這些在新 DB 上回 `False`（已存在，免動作）。

### 7.3 Flag 換代策略

```python
OLD_FLAG = DEFAULT_CENTRAL_DIR / ".migration-done"       # v2
MIGRATION_FLAG = DEFAULT_CENTRAL_DIR / ".migration-v3-done"  # v3

def _should_run_migrations(conn) -> bool:
    # 若 v3 flag 已存在 → skip
    if MIGRATION_FLAG.exists():
        return False
    return True

# 成功跑完後:
MIGRATION_FLAG.touch()
# 刻意不刪 OLD_FLAG（讓使用者 downgrade 回 v2.3 時不會誤觸發 legacy 掃描）
```

---

## 8. 效能與容量評估

### 8.1 資料量預估

| 表 | 每筆大小 | 典型日產量 | 年量估算 |
|----|---------|-----------|---------|
| `context_saves` | ~2 KB（title+content 平均） | 20 筆（含 auto-dump） | ~15 MB |
| `context_diagnoses` | ~3 KB（findings_json） | 5-10 筆 | ~8 MB |
| `context_compact_events` | 80 B | 2-5 筆 | <1 MB |

年量 < 25 MB，SQLite 綽綽有餘。

### 8.2 預期查詢 Hot Path

| 查詢 | 使用索引 | 估 cost |
|------|---------|--------|
| `ctx-reinject.py` 找最新未還原 auto-dump | `idx_saves_restored` + `idx_saves_category` | O(log N) |
| Dashboard 列最新診斷 | `idx_diag_ran` | O(log N) |
| Persistent tab 列長效筆記 | `idx_saves_persistent` | O(log N) |
| Timeline 列近 30 天 compact | `idx_compact_happened` | O(log N) |

所有 hot path 都走索引，無全表掃描。

### 8.3 可能的瓶頸

- `findings_json` TEXT 欄位若大量 JSON 字串且頻繁全文檢索會慢 — v3 不做全文檢索（搜尋只查 `context_saves.title/content` 沿用 v2.3 行為）
- Web Viewer 若直接 `SELECT * FROM context_diagnoses` 一次撈 100 筆 findings_json，payload 可能破 300KB — 列表頁只選必要欄位，findings_json 延後載入

---

## 9. Rollback 策略

SQLite 不支援 `DROP COLUMN`（3.35+ 才支援，但為避免依賴版本），rollback 採「新表重建」方式。db.sql 附 rollback 區塊，但**不建議執行** — 會遺失 v3 資料。

建議的回退方式：使用者 downgrade 到 v2.3 plugin 後，v2 的 `ctx-db.py` 會**無視**新欄位，舊功能仍可用。新表（`context_diagnoses` / `context_compact_events`）在 v2.3 下不被讀取，但不會造成錯誤。

---

## 10. 檢核清單

實作 M1/M2/M4 的 migration 前須確認：

- [ ] 每個新 migration 函式是冪等的（跑兩次不會出錯）
- [ ] `migrate_add_*` 先 `PRAGMA table_info()` 再 ALTER
- [ ] `migrate_create_*` 用 `CREATE TABLE IF NOT EXISTS`
- [ ] 所有新欄位要嘛 NULL-tolerant 要嘛有 DEFAULT（SQLite `ALTER ADD NOT NULL` 限制）
- [ ] 新索引全用 `CREATE INDEX IF NOT EXISTS`
- [ ] DML 變更（`clean`）的 SQL 在兩個 migration 狀態下都正確（跑前 / 跑後都能用）
- [ ] `.migration-v3-done` flag 落點正確
- [ ] `_init_schema` CREATE TABLE 語句包含 v3 全欄位（給新 DB）
- [ ] Downgrade 至 v2.3 plugin 後舊功能仍能讀既有 context_saves（v3 新欄位被無視）

---

## 11. 與 spec.md 對應

| db.md 章節 | spec.md 章節 |
|------------|--------------|
| §4 context_saves 擴充 | §4.1 |
| §5 context_diagnoses | §4.2 + §6.3 五類 token |
| §6 context_compact_events | §4.3 + §7 閉環 |
| §7 Migration 流程 | §4.4 + §9 相容性 |

---

**文件終**
