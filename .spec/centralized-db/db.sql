-- ============================================================
--  ctx-save 集中式 DB（v2.1）SQL 腳本
--  Feature slug : centralized-db
--  Target engine: SQLite 3.31+
--  DB path      : ~/.ctx-save/context.db（可由 CTX_SAVE_DB_PATH 覆寫）
--
--  本腳本包含以下區塊：
--    [1] PRAGMA 設定
--    [2] CREATE TABLE（新使用者）
--    [3] CREATE INDEX（新 + 舊）
--    [4] ALTER TABLE（既有使用者升級用，冪等）
--    [5] migrate-legacy 片段範例
--    [6] 範例資料
--    [7] Rollback SQL
-- ============================================================


-- ============================================================
-- [1] PRAGMA 設定（所有連線開啟時套用，不是 DDL；此處僅作提示）
-- ============================================================
-- 以下四條應由應用層（ctx-db.py / ctx-viewer.py 的 _connect_db）
-- 在每次 sqlite3.connect() 之後立即執行：
--
--   PRAGMA journal_mode = WAL;
--   PRAGMA synchronous  = NORMAL;
--   PRAGMA busy_timeout = 5000;
--   PRAGMA foreign_keys = ON;
--
-- 寫端需改用 `BEGIN IMMEDIATE;` 而非 `BEGIN;`，以立即取得 write lock。


-- ============================================================
-- [2] CREATE TABLE（新使用者初始化；既有 DB 由 IF NOT EXISTS 跳過）
-- ============================================================
CREATE TABLE IF NOT EXISTS context_saves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT    NOT NULL,                              -- 應用層驗證 ^[A-Za-z0-9_-]{1,64}$
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    project_path        TEXT    NOT NULL,                              -- v2.1 新增；絕對路徑（POSIX 風格）
    context_percentage  REAL,
    title               TEXT    NOT NULL,
    category            TEXT    NOT NULL,                              -- 寫入時白名單 ^[a-z0-9_-]{1,32}$，不符改 custom
    content             TEXT    NOT NULL,
    tags                TEXT    DEFAULT '',
    model               TEXT    DEFAULT '',
    deleted_at          DATETIME                                       -- v2.0 已加入；NULL = 未刪除
);


-- ============================================================
-- [3] CREATE INDEX（冪等；同時覆蓋新使用者與升級使用者）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_saves_session   ON context_saves(session_id);
CREATE INDEX IF NOT EXISTS idx_saves_project   ON context_saves(project_path);
CREATE INDEX IF NOT EXISTS idx_saves_created   ON context_saves(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saves_category  ON context_saves(category);
CREATE INDEX IF NOT EXISTS idx_saves_deleted   ON context_saves(deleted_at);

-- v2.1 新增：支撐 /api/sessions?project_path=X 主查詢 + sidebar 過濾
CREATE INDEX IF NOT EXISTS idx_saves_project_created
    ON context_saves(project_path, created_at DESC);


-- ============================================================
-- [4] ALTER TABLE（既有使用者升級）— 由 Python 端包 PRAGMA table_info 保護冪等
-- ============================================================
-- SQLite 不支援「NOT NULL 且無預設」的 ADD COLUMN；
-- 因此先加 nullable → 回填 → 由應用層於 INSERT 時強制必帶。
--
-- 等效 Python 實作（migrate_add_project_path）：
--   cols = [r[1] for r in conn.execute("PRAGMA table_info(context_saves)").fetchall()]
--   if "project_path" not in cols:
--       conn.execute("ALTER TABLE context_saves ADD COLUMN project_path TEXT")
--       conn.execute("UPDATE context_saves SET project_path = '(unknown)' WHERE project_path IS NULL")

-- 純 SQL 版（若手動執行，請先確認欄位不存在再跑）：
ALTER TABLE context_saves ADD COLUMN project_path TEXT;
UPDATE context_saves SET project_path = '(unknown)' WHERE project_path IS NULL;


-- ============================================================
-- [5] migrate-legacy 片段範例
--     （每個 legacy DB 都重複以下三段；Python 端迴圈執行）
-- ============================================================

-- [5.1] 附掛 legacy DB
ATTACH DATABASE '/Users/cheng/IdeaProjects/Taipei/LineBC/.ctx-save/context.db' AS legacy;

-- [5.2] 去重 INSERT（natural key = session_id + created_at + title）
--       若 legacy 無 project_path，COALESCE 以 fallback（legacy DB 父目錄 parent）填入
INSERT INTO main.context_saves (
    session_id, created_at, project_path,
    context_percentage, title, category, content, tags, model, deleted_at
)
SELECT
    l.session_id,
    l.created_at,
    COALESCE(l.project_path, '/Users/cheng/IdeaProjects/Taipei/LineBC'),
    l.context_percentage,
    l.title,
    l.category,
    l.content,
    l.tags,
    l.model,
    l.deleted_at
  FROM legacy.context_saves AS l
 WHERE NOT EXISTS (
    SELECT 1
      FROM main.context_saves AS m
     WHERE m.session_id = l.session_id
       AND m.created_at = l.created_at
       AND m.title      = l.title
 );

-- [5.3] 卸載
DETACH DATABASE legacy;

-- [5.4] 改名 legacy 檔（Python 端執行 os.rename，此處僅示意）
-- mv /Users/cheng/IdeaProjects/Taipei/LineBC/.ctx-save/context.db \
--    /Users/cheng/IdeaProjects/Taipei/LineBC/.ctx-save/context.db.migrated-20260418

-- [5.5] 寫旗標檔（Python 端執行）
-- touch ~/.ctx-save/.migration-done


-- ============================================================
-- [6] 範例資料（測試用）
-- ============================================================

-- (1) 合法 category：直接入庫
INSERT INTO context_saves (session_id, project_path, title, category, content, tags, model, context_percentage)
VALUES (
    'sess_abc123',
    '/Users/cheng/IdeaProjects/Taipei/LineBC',
    '訂閱系統與 LineBC 整合筆記',
    'integration',
    '透過 BcPushService 呼叫 /API/push，訂閱系統與 LineBC 為獨立 DB ...',
    'subscribe,push',
    'claude-opus-4-7',
    0.42
);

-- (2) 邊緣值（模擬 category = 'MSSQL_IndexDesign' 被白名單改為 custom 後的入庫樣貌）
INSERT INTO context_saves (session_id, project_path, title, category, content, tags, model, context_percentage)
VALUES (
    'sess_def456',
    '/Users/cheng/IdeaProjects/Fubon-LineBC',
    'MSSQL 索引設計',
    'custom',
    '[原 category: MSSQL_IndexDesign]' || char(10) || '以 (project_path, created_at DESC) 作為主力複合索引 ...',
    'mssql,index',
    'claude-sonnet-4-6',
    0.28
);

-- (3) 軟刪除資料（模擬已刪）
INSERT INTO context_saves (session_id, project_path, title, category, content, deleted_at)
VALUES (
    'sess_ghi789',
    '/Users/cheng/IdeaProjects/Abbott-PED-LineBC',
    '過時的測試筆記',
    'task',
    '此筆應被 /api/sessions 預設 WHERE deleted_at IS NULL 過濾掉',
    '2026-04-17 10:00:00'
);


-- ============================================================
-- [7] Rollback SQL
-- ============================================================

-- [7.1] 最保守的回退：僅移除 v2.1 新增的複合索引
DROP INDEX IF EXISTS idx_saves_project_created;

-- [7.2] 完全回退到 v2.0 schema（移除 project_path 欄位）
--
--       選項 A：SQLite 3.35+ 支援 DROP COLUMN
--       -------------------------------------
-- ALTER TABLE context_saves DROP COLUMN project_path;
--
--       選項 B：SQLite < 3.35 的手動重建流程
--       -------------------------------------
-- BEGIN IMMEDIATE;
--
-- CREATE TABLE context_saves_v20 (
--     id                  INTEGER PRIMARY KEY AUTOINCREMENT,
--     session_id          TEXT    NOT NULL,
--     created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
--     context_percentage  REAL,
--     title               TEXT    NOT NULL,
--     category            TEXT    NOT NULL,
--     content             TEXT    NOT NULL,
--     tags                TEXT    DEFAULT '',
--     model               TEXT    DEFAULT '',
--     deleted_at          DATETIME
-- );
--
-- INSERT INTO context_saves_v20 (
--     id, session_id, created_at, context_percentage,
--     title, category, content, tags, model, deleted_at
-- )
-- SELECT
--     id, session_id, created_at, context_percentage,
--     title, category, content, tags, model, deleted_at
--   FROM context_saves;
--
-- DROP TABLE context_saves;
-- ALTER TABLE context_saves_v20 RENAME TO context_saves;
--
-- CREATE INDEX IF NOT EXISTS idx_saves_session   ON context_saves(session_id);
-- CREATE INDEX IF NOT EXISTS idx_saves_created   ON context_saves(created_at DESC);
-- CREATE INDEX IF NOT EXISTS idx_saves_category  ON context_saves(category);
-- CREATE INDEX IF NOT EXISTS idx_saves_deleted   ON context_saves(deleted_at);
--
-- COMMIT;

-- [7.3] 整個資料表移除（僅極端情況，會丟失所有資料 — 慎用）
-- DROP TABLE IF EXISTS context_saves;

-- [7.4] Legacy DB 還原：將 .migrated-YYYYMMDD 改名回 context.db（OS 層面，Python 執行）
-- mv /path/to/{project}/.ctx-save/context.db.migrated-20260418 \
--    /path/to/{project}/.ctx-save/context.db
-- rm ~/.ctx-save/.migration-done

-- ============================================================
-- End of db.sql
-- ============================================================
