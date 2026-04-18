---
generated: 2026-04-18
source: plan-build（3 位 Subagent 並行產出）
arch_ref: arch.md
---

# 程式碼清單

## 新增檔案

本次為既有三個腳本的完整改寫（不是新增 Python 模組），因此「新增檔案」限於規劃產物本身：

| 檔案路徑 | 層級 | 說明 |
|---------|------|------|
| `.spec/centralized-db/spec.md` | 規格 | 技術規格書（API、業務邏輯、判斷區塊） |
| `.spec/centralized-db/db.md` | DB 設計 | 表結構、索引、migration 步驟 |
| `.spec/centralized-db/db.sql` | DB 設計 | CREATE/ALTER/索引 SQL + Rollback |
| `.spec/centralized-db/arch.md` | 架構設計 | 12 章 + 4 Mermaid；類別/函式清單、介面契約 |
| `.spec/centralized-db/files.md` | 程式碼清單 | 本檔 |

## 修改檔案

| 檔案路徑 | 層級 | 行數 | 修改說明 |
|---------|------|------|---------|
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py` | Data Layer（CLI + SQLite） | 969 | DB 路徑改為 `~/.ctx-save/context.db`；新增 `_connect_db(writer)` 套用 WAL + synchronous=NORMAL + busy_timeout=5000 + foreign_keys=ON；`isolation_level=None` 讓 writer 顯式 `BEGIN IMMEDIATE`；schema 加 `project_path` 欄位與 `idx_saves_project_created` 複合索引；`category` 白名單正規化（`^[a-z0-9_-]{1,32}$`，不符改 `custom`）；`session_id` 驗證（`^[A-Za-z0-9_-]{1,64}$`）；新增 CLI `list-projects` 與 `migrate-legacy [--dry-run] [--yes]`；`_scan_legacy_dbs` 限定工作目錄掃描（`_legacy_scan_roots()`，預設 IdeaProjects/Projects/workspace/code/src，可由 `CTX_SAVE_MIGRATE_ROOTS` env 覆寫）以避免家目錄全域 rglob 導致的 hang；ATTACH + INSERT WHERE NOT EXISTS 去重合併；`maybe_auto_migrate` 可由 `CTX_SAVE_NO_MIGRATE=1` 關閉 |
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-viewer.py` | Web Layer + Frontend | 2078 | 816 行 Python（HTTP server + 9 個 API）+ 1262 行 HTML_PAGE；importlib 載入 dash-named `ctx-db.py` 作為共用模組（`CTX_DB._connect_db / _run_all_migrations / list_projects / _validate_session_id / init_db`）；header 顯示 DB 路徑（home→`~`）與版本；sidebar 新增「專案」下拉（`/api/projects`）；`/api/sessions` 支援 `project_path` 篩選 + `LIMIT 500`（上限保護）；`/api/search` 關鍵字長度 ≤200；POST/DELETE 增 Origin/Referer 白名單（127.0.0.1 / localhost）與 64KB body 上限；`/api/saves/batch-delete` 以 HMAC-SHA256（`CONFIRM_SECRET=secrets.token_bytes(32)` + `STARTED_AT` salt）產出 stateless confirm_token，後端 5000 筆分段 DELETE；前端全面改用 `addEventListener + dataset`，`escapeHtml`（含 `&#39;`）+ `escapeJs`（`JSON.stringify`），徹底 remove 字串 onclick 拼接；batch delete 預覽時 snapshot filter，confirm 不一致強制重預覽（Q3-4）；`_handle_ping` 在 DB busy 回 200 `ok=false`（launcher healthcheck 不會被誤判為掛掉） |
| `plugins/ctx-save/skills/ctx-view/scripts/ctx-view-launcher.py` | Launcher Layer | 608 | 移除「由 cwd 往上找 `.ctx-save/context.db`」邏輯；改為 `CENTRAL_DB = ~/.ctx-save/context.db`，DB 不存在時呼叫 `ctx_db.init_db()` 自動建立；支援 `CTX_VIEW_DB` / `CTX_SAVE_DB_PATH` 覆寫（`_resolve_db_override`）；`_cleanup_stale_pid` 檢測孤兒 PID file；`try_reuse_existing(expected_db=...)` 比對 PID file 內 DB 路徑與目前解析結果，不一致則強制重啟；`_print_banner` 家目錄相對路徑顯示（`_homeify`）與版本標示（從 ping 抓取，預設 `LAUNCHER_VERSION="v2.1"`） |

## Plugin 設定（未修改）

| 檔案 | 狀態 | 備註 |
|------|------|------|
| `plugins/ctx-save/.claude-plugin/plugin.json` | 未改 | 版號仍為 2.0.0；發布前需人工升版到 2.1.0 |
| `plugins/ctx-save/skills/ctx-save/SKILL.md` | 未改 | Skill 說明待補 `list-projects` / `migrate-legacy` 的使用範例 |
| `plugins/ctx-save/.gitignore` | 未改 | 需追加 `.ctx-save/` 或在 plugin 根 `.gitignore` 追加 |

## 驗收要點（對應成功準則）

| # | 驗收項 | 對應檔案 | 驗證方式 |
|---|--------|---------|---------|
| 1 | 跨專案寫入皆落中央 DB | `ctx-db.py:save` | 已於 smoke test 驗證：`CTX_PROJECT` 帶不同路徑 → `list-projects` 回傳多專案 |
| 2 | Header 顯示 DB 路徑與版本 | `ctx-viewer.py` HTML_PAGE | 待 E2E（啟動 viewer 後瀏覽） |
| 3 | 專案下拉過濾 | `/api/projects` + `/api/sessions?project_path=` | 待 E2E |
| 4 | legacy 合併 | `ctx-db.py:migrate_legacy` | smoke test：`migrate-legacy --dry-run` 行為正確 |
| 5 | CSRF 拒絕跨 origin | `_check_csrf` | 待 E2E（`curl -H 'Origin: https://evil.com'`） |
| 6 | XSS 不執行 | 前端 escapeHtml / dataset | 待 E2E |

## 後續（不在本次 plan-build 範圍）

- E2E 驗收（`/plan-verify`）
- 3 人 Agent Teams 審查（`/plan-review`）
- plugin.json 升版 + SKILL.md 文件更新（`/plan-close` 階段或單獨提交）
