-- =============================================================================
-- ctx-save v3 — 資料庫 Schema SQL
-- Target: SQLite 3 (Python stdlib sqlite3)
-- Path:   ~/.ctx-save/context.db
-- Upstream: spec.md §4, db.md §3-§7
-- Date:   2026-04-21
-- =============================================================================
--
-- 本檔內容可分段套用：
--   Section A. 全新安裝（v3 clean install）       → 只跑 Section A
--   Section B. v2.3 → v3 漸進 migration（idempotent）→ 只跑 Section B
--   Section C. 範例資料（開發驗證用）             → 選擇性
--   Section D. Rollback / Downgrade（緊急回退用） → 破壞性，預設不執行
--
-- 實作入口：plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py
--   - _init_schema()      : Section A
--   - _run_all_migrations(): Section B
-- =============================================================================


-- =============================================================================
-- Section A. 全新安裝（clean install for v3）
-- 對「首次建立的 ~/.ctx-save/context.db」使用。
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = OFF;   -- 刻意保持鬆耦合，delete 原 snapshot 不影響 distill 版本

-- -----------------------------------------------------------------------------
-- A.1 主表 context_saves（v3 完整欄位）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_saves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    project_path        TEXT NOT NULL DEFAULT '(unknown)',
    context_percentage  REAL,
    title               TEXT NOT NULL,
    category            TEXT NOT NULL,
    content             TEXT NOT NULL,
    tags                TEXT DEFAULT '',
    model               TEXT DEFAULT '',
    deleted_at          DATETIME,
    -- v3 新增
    is_persistent       INTEGER NOT NULL DEFAULT 0,   -- 0/1 長效筆記旗標（N4）
    restored_at         DATETIME,                     -- 最近一次 auto-reinject 時間（N1）
    distilled_from_id   INTEGER                       -- 若為 distilled 版本，指向原始 id（N3）
);

CREATE INDEX IF NOT EXISTS idx_saves_session         ON context_saves(session_id);
CREATE INDEX IF NOT EXISTS idx_saves_project         ON context_saves(project_path);
CREATE INDEX IF NOT EXISTS idx_saves_created         ON context_saves(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saves_category        ON context_saves(category);
CREATE INDEX IF NOT EXISTS idx_saves_deleted         ON context_saves(deleted_at);
CREATE INDEX IF NOT EXISTS idx_saves_project_created ON context_saves(project_path, created_at DESC);
-- v3 新增索引
CREATE INDEX IF NOT EXISTS idx_saves_persistent      ON context_saves(is_persistent);
CREATE INDEX IF NOT EXISTS idx_saves_restored        ON context_saves(restored_at DESC);
CREATE INDEX IF NOT EXISTS idx_saves_distilled       ON context_saves(distilled_from_id);

-- -----------------------------------------------------------------------------
-- A.2 診斷結果表 context_diagnoses（M2 N2 analyze）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_diagnoses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id            TEXT NOT NULL,
    project_path          TEXT NOT NULL DEFAULT '(unknown)',
    ran_at                DATETIME DEFAULT CURRENT_TIMESTAMP,
    context_percentage    REAL,
    score                 INTEGER,                -- 0-100
    tokens_conversation   INTEGER,
    tokens_files          INTEGER,
    tokens_tools_schema   INTEGER,                -- 啟動載入的 tool definitions
    tokens_tools_runtime  INTEGER,                -- Bash/WebFetch/MCP call+result
    tokens_system         INTEGER,
    findings_json         TEXT NOT NULL,          -- json.dumps(findings_list)
    suggest_focus         TEXT,                   -- '/compact focus on X,Y' 的 X,Y
    deleted_at            DATETIME
);

CREATE INDEX IF NOT EXISTS idx_diag_session      ON context_diagnoses(session_id);
CREATE INDEX IF NOT EXISTS idx_diag_ran          ON context_diagnoses(ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_project_ran  ON context_diagnoses(project_path, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_deleted      ON context_diagnoses(deleted_at);

-- -----------------------------------------------------------------------------
-- A.3 Compact 事件索引表 context_compact_events（M4 選做）
-- 只在 N6 Web Timeline 效能不足時才建；M3 之前可 query context_saves 頂著。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_compact_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    project_path TEXT NOT NULL DEFAULT '(unknown)',
    happened_at  DATETIME NOT NULL,
    trigger      TEXT,                       -- 'auto' / 'manual'（Claude Code PreCompact hook 帶入）
    context_pct  REAL,                       -- compact 當下 context 使用率
    snapshot_id  INTEGER,                    -- 對應 context_saves.id（鬆耦合，不建 FK）
    reinjected   INTEGER NOT NULL DEFAULT 0, -- 0/1，ctx-reinject.py 成功後改 1
    deleted_at   DATETIME
);

CREATE INDEX IF NOT EXISTS idx_compact_happened   ON context_compact_events(happened_at DESC);
CREATE INDEX IF NOT EXISTS idx_compact_session    ON context_compact_events(session_id);
CREATE INDEX IF NOT EXISTS idx_compact_reinjected ON context_compact_events(reinjected);
CREATE INDEX IF NOT EXISTS idx_compact_deleted    ON context_compact_events(deleted_at);


-- =============================================================================
-- Section B. v2.3 → v3 漸進 Migration（idempotent）
-- 對「已存在的 v2.3 context.db」使用。
-- 每一段前都先檢查欄位/表是否存在，不存在才執行。
-- SQLite 的 ADD COLUMN 不支援 IF NOT EXISTS，故由應用層（ctx-db.py）
-- 透過 PRAGMA table_info() 判斷，以下 SQL 用於手動操作或對照。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- B.1 context_saves 擴欄位
-- -----------------------------------------------------------------------------

-- [執行前請先確認欄位不存在]
-- ALTER TABLE context_saves ADD COLUMN is_persistent INTEGER NOT NULL DEFAULT 0;
-- CREATE INDEX IF NOT EXISTS idx_saves_persistent ON context_saves(is_persistent);

-- [執行前請先確認欄位不存在]
-- ALTER TABLE context_saves ADD COLUMN restored_at DATETIME;
-- CREATE INDEX IF NOT EXISTS idx_saves_restored ON context_saves(restored_at DESC);

-- [執行前請先確認欄位不存在]
-- ALTER TABLE context_saves ADD COLUMN distilled_from_id INTEGER;
-- CREATE INDEX IF NOT EXISTS idx_saves_distilled ON context_saves(distilled_from_id);

-- 對照 Python 實作：ctx-db.py
--
--   def migrate_add_persistent(conn) -> bool:
--       cursor = conn.execute("PRAGMA table_info(context_saves)")
--       columns = {row[1] for row in cursor.fetchall()}
--       if "is_persistent" in columns:
--           return False
--       conn.execute("ALTER TABLE context_saves ADD COLUMN is_persistent INTEGER NOT NULL DEFAULT 0")
--       conn.execute("CREATE INDEX IF NOT EXISTS idx_saves_persistent ON context_saves(is_persistent)")
--       return True
--
--   def migrate_add_restored_at(conn) -> bool: ...
--   def migrate_add_distilled_from(conn) -> bool: ...

-- -----------------------------------------------------------------------------
-- B.2 建立 context_diagnoses（M2 啟用）
-- -----------------------------------------------------------------------------
-- 與 A.2 相同，CREATE TABLE IF NOT EXISTS 本身即 idempotent
CREATE TABLE IF NOT EXISTS context_diagnoses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id            TEXT NOT NULL,
    project_path          TEXT NOT NULL DEFAULT '(unknown)',
    ran_at                DATETIME DEFAULT CURRENT_TIMESTAMP,
    context_percentage    REAL,
    score                 INTEGER,
    tokens_conversation   INTEGER,
    tokens_files          INTEGER,
    tokens_tools_schema   INTEGER,
    tokens_tools_runtime  INTEGER,
    tokens_system         INTEGER,
    findings_json         TEXT NOT NULL,
    suggest_focus         TEXT,
    deleted_at            DATETIME
);

CREATE INDEX IF NOT EXISTS idx_diag_session     ON context_diagnoses(session_id);
CREATE INDEX IF NOT EXISTS idx_diag_ran         ON context_diagnoses(ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_project_ran ON context_diagnoses(project_path, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_deleted     ON context_diagnoses(deleted_at);

-- -----------------------------------------------------------------------------
-- B.3 建立 context_compact_events（M4 啟用）
-- -----------------------------------------------------------------------------
-- M3 之前可延後此段；M4 再啟用

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

CREATE INDEX IF NOT EXISTS idx_compact_happened   ON context_compact_events(happened_at DESC);
CREATE INDEX IF NOT EXISTS idx_compact_session    ON context_compact_events(session_id);
CREATE INDEX IF NOT EXISTS idx_compact_reinjected ON context_compact_events(reinjected);
CREATE INDEX IF NOT EXISTS idx_compact_deleted    ON context_compact_events(deleted_at);


-- =============================================================================
-- Section C. 範例資料（開發 / 測試用；正式 install 不執行）
-- =============================================================================

-- C.1 一筆一般快照
INSERT INTO context_saves (session_id, project_path, context_percentage, title, category, content, tags)
VALUES (
    'sess-demo-0001',
    '/Users/demo/projects/push',
    62.5,
    '完成 PushService 串接 LineBC',
    'change',
    'feat(push): 新增 LineBC 呼叫，加重試機制 ...',
    'push,linebc'
);

-- C.2 一筆 auto-dump（由 PreCompact hook 產生）
INSERT INTO context_saves (session_id, project_path, context_percentage, title, category, content)
VALUES (
    'sess-demo-0001',
    '/Users/demo/projects/push',
    82.3,
    '[auto-dump] 對話快照 2026-04-21 10:15',
    'auto-dump',
    '... raw transcript tail (200KB) ...'
);

-- C.3 一筆被 reinject 過的 auto-dump（restored_at 已填）
UPDATE context_saves
SET restored_at = '2026-04-21 10:20:15'
WHERE session_id = 'sess-demo-0001'
  AND category = 'auto-dump';

-- C.4 一筆長效筆記（is_persistent=1）
INSERT INTO context_saves (session_id, project_path, context_percentage, title, category, content, is_persistent)
VALUES (
    'sess-demo-0001',
    '/Users/demo/projects/push',
    NULL,
    '專案架構決策：MQ 選 RabbitMQ（2026-04-15）',
    'decision',
    '選 RabbitMQ 的理由：5 萬人推播需要背壓控制 ...',
    1
);

-- C.5 一筆 distill 衍生（distilled_from_id 指向 auto-dump）
INSERT INTO context_saves (session_id, project_path, title, category, content, distilled_from_id)
SELECT
    session_id,
    project_path,
    '[distilled] 對話快照 2026-04-21 10:15（瘦身版）',
    'auto-dump',
    '精煉後重點：主要改 PushService.java 的重試策略，討論 3 輪後決議 ...',
    id
FROM context_saves
WHERE session_id = 'sess-demo-0001'
  AND category = 'auto-dump'
LIMIT 1;

-- C.6 一筆診斷結果
INSERT INTO context_diagnoses (
    session_id, project_path, context_percentage, score,
    tokens_conversation, tokens_files, tokens_tools_schema, tokens_tools_runtime, tokens_system,
    findings_json, suggest_focus
) VALUES (
    'sess-demo-0001',
    '/Users/demo/projects/push',
    73.0,
    81,
    19000, 12000, 5000, 8000, 6000,
    '[{"type":"duplicate_read","severity":"medium","file":"PushService.java","count":6,"estimated_tokens":2100,"root_cause_key":"duplicate_read:PushService.java"}]',
    'PushService.java,classifier.py'
);

-- C.7 一筆 compact 事件
INSERT INTO context_compact_events (session_id, project_path, happened_at, trigger, context_pct, snapshot_id, reinjected)
SELECT
    session_id,
    project_path,
    '2026-04-21 10:15:02',
    'auto',
    82.3,
    id,
    1
FROM context_saves
WHERE session_id = 'sess-demo-0001'
  AND category = 'auto-dump'
  AND distilled_from_id IS NULL
LIMIT 1;


-- =============================================================================
-- Section D. Rollback / Downgrade（破壞性，預設不執行）
--
-- ⚠️ 本段會遺失 v3 新增的資料（is_persistent=1 的長效筆記、所有診斷結果、compact 事件）。
-- 執行前務必備份 ~/.ctx-save/context.db。
--
-- 推薦的 downgrade 方式：直接換回 v2.3 plugin
--   - v2.3 的 ctx-db.py 會「無視」新欄位（不會讀也不會寫）
--   - 新表 context_diagnoses / context_compact_events 在 v2.3 下被閒置，不會造成錯誤
--   - 以下 rollback SQL 只在「需要回到乾淨 v2.3 schema」時使用
-- =============================================================================

-- D.1 刪除 v3 新表
DROP INDEX IF EXISTS idx_compact_deleted;
DROP INDEX IF EXISTS idx_compact_reinjected;
DROP INDEX IF EXISTS idx_compact_session;
DROP INDEX IF EXISTS idx_compact_happened;
DROP TABLE IF EXISTS context_compact_events;

DROP INDEX IF EXISTS idx_diag_deleted;
DROP INDEX IF EXISTS idx_diag_project_ran;
DROP INDEX IF EXISTS idx_diag_ran;
DROP INDEX IF EXISTS idx_diag_session;
DROP TABLE IF EXISTS context_diagnoses;

-- D.2 移除 v3 新增欄位（SQLite 3.35+ 才支援 DROP COLUMN；更早版本需重建）
--
-- [若 SQLite >= 3.35]
-- DROP INDEX IF EXISTS idx_saves_distilled;
-- DROP INDEX IF EXISTS idx_saves_restored;
-- DROP INDEX IF EXISTS idx_saves_persistent;
-- ALTER TABLE context_saves DROP COLUMN distilled_from_id;
-- ALTER TABLE context_saves DROP COLUMN restored_at;
-- ALTER TABLE context_saves DROP COLUMN is_persistent;
--
-- [若 SQLite < 3.35，用「新表重建」法]
-- BEGIN TRANSACTION;
-- CREATE TABLE context_saves_v2 (
--     id INTEGER PRIMARY KEY AUTOINCREMENT,
--     session_id TEXT NOT NULL,
--     created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
--     project_path TEXT NOT NULL DEFAULT '(unknown)',
--     context_percentage REAL,
--     title TEXT NOT NULL,
--     category TEXT NOT NULL,
--     content TEXT NOT NULL,
--     tags TEXT DEFAULT '',
--     model TEXT DEFAULT '',
--     deleted_at DATETIME
-- );
-- INSERT INTO context_saves_v2
--     SELECT id, session_id, created_at, project_path, context_percentage,
--            title, category, content, tags, model, deleted_at
--     FROM context_saves;
-- DROP TABLE context_saves;
-- ALTER TABLE context_saves_v2 RENAME TO context_saves;
-- -- 重建 v2.3 索引
-- CREATE INDEX idx_saves_session         ON context_saves(session_id);
-- CREATE INDEX idx_saves_project         ON context_saves(project_path);
-- CREATE INDEX idx_saves_created         ON context_saves(created_at DESC);
-- CREATE INDEX idx_saves_category        ON context_saves(category);
-- CREATE INDEX idx_saves_deleted         ON context_saves(deleted_at);
-- CREATE INDEX idx_saves_project_created ON context_saves(project_path, created_at DESC);
-- COMMIT;

-- D.3 移除 v3 migration flag，讓下次啟動重跑 migration（若決定回 v3）
-- [shell]
-- rm -f ~/.ctx-save/.migration-v3-done
-- [/shell]

-- =============================================================================
-- 文件終
-- =============================================================================
