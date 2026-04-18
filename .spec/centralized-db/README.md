---
type: feature
name: ctx-save 集中式 DB
slug: centralized-db
status: 已完成
notion_url: (未註冊於 Notion 工作區 — 個人 plugin 基礎設施)
notion_page_id:
branch: feature/ctx-save-centralized-db
tech_stack: python-stdlib
repo: github.com/mark22013333/forge
plugin_path: plugins/ctx-save
created: 2026-04-18
---

# ctx-save 集中式 DB

## 背景（本次重啟原因）

前一輪工作已在對話中完成「centralized DB v2.1」的設計與實作，但 artefacts 從未落盤到 forge repo（`master` 為 clean，無相關 commit；`.spec/centralized-db/` 亦不存在）。本次從頭建立規格與實作。

## 需求描述

目前 `ctx-save` 使用「每專案一個 DB」（`{project}/.ctx-save/context.db`），造成：

1. **無法跨 session / 跨專案查詢** — viewer 從 cwd 往上只找最近的一個 DB
2. **網頁無法識別目前載入哪個 DB** — header 沒顯示 DB 路徑或專案名稱
3. **Session 碎片化** — 同一段對話若換 cwd 會存到不同 DB

### 目標

- DB 改集中至 `~/.ctx-save/context.db`（單一 SQLite）
- 每筆記錄透過 `project_path` 欄位區分來源專案
- Viewer 提供專案下拉過濾 + header 顯示目前 DB 路徑 + 版本
- 既有各專案 `.ctx-save/context.db` 有遷移腳本自動匯入
- CLI（`/ctx-save`）寫入時自動帶 cwd 為 project_path

### In Scope

- `ctx-db.py`：DB 路徑改為 `~/.ctx-save/context.db`；新增 `_connect_db(writer)` 支援 WAL + busy_timeout + BEGIN IMMEDIATE
- `ctx-viewer.py`：header 顯示 DB 路徑；sidebar 加「專案」下拉；`/api/sessions` 支援 `project_path` 篩選
- `ctx-view-launcher.py`：檢測 stale PID 並清理；支援 `CTX_VIEW_DB` 環境變數覆寫路徑
- 遷移腳本：`ctx-db.py migrate-legacy` 掃描 `~/IdeaProjects/**/.ctx-save/context.db` 合併進中央 DB（避免重複）
- 安全修補（同步處理，避免再審查）：
  - **S3-1**：`session_id` 與 `title` 在前端 onclick 改用 dataset + addEventListener
  - **S3-2**：POST/DELETE 檢查 `Origin`/`Referer` = `127.0.0.1` or `localhost`
  - **S3-7**：`category` 後端白名單（`^[a-z0-9_-]{1,32}$`，不符改 `custom`）
  - **Q3-4**：前端 batch delete 預覽後快照 filter；confirm 時比對，不一致要求重新預覽
- `.gitignore` 在 plugin 內加 `.ctx-save/`（避免誤 commit）

### Out of Scope

- 集中式 DB 的密碼保護、加密儲存（之後若要分享 DB 再談）
- 全文搜尋（FTS5 索引）— 先用 LIKE 夠用
- 多使用者 shared DB — 只管單機集中

## 判斷

- FRONTEND_REQUIRED: true（viewer UI 改動）
- FRONTEND_TECH: 原生 HTML/JS（Python http.server 嵌入字串）
- DB_REQUIRED: true（schema 加 `project_path` 欄位與索引）
- DB_TABLES: `context_saves`（修改）、無新表

## 相關 Round-3 審查發現（需合併修復）

| 編號 | 嚴重度 | 內容 | 本 feature 處理方式 |
|---|---|---|---|
| B-0 | Blocker | 前輪 v2.1 工作未落盤 | 本次重做 |
| S3-1 | High | onclick XSS via session_id | In scope（UI 重寫） |
| S3-2 | High | 無 Origin/Referer 檢查，CSRF 可遠端刪庫 | In scope |
| S3-7 | Medium | category innerHTML XSS | In scope（後端白名單 + 前端 textContent） |
| Q3-4 | Medium | batch delete preview/confirm filter desync | In scope |
| S3-3 | Medium | OperationalError 原訊息洩漏 | In scope（回 `"db busy or readonly"`） |
| S3-4 | Medium | POST body 無大小上限 | In scope（64KB 限制） |
| P3-1 | Medium | 單一 DELETE 無分段 | In scope（5000 筆分段） |
| P3-3 | Medium | `/api/sessions` 無 LIMIT | In scope（LIMIT 500） |
| S3-5 | Low | search keyword 無長度上限 | In scope（200 字元上限） |

## 成功準則

1. `ctx-save` 從 LineBC 和任一其他專案目錄執行，資料都落到 `~/.ctx-save/context.db`
2. Viewer 開啟顯示 header：`⚙ Context Save Viewer — DB: ~/.ctx-save/context.db — v2.1`
3. Sidebar 有「全部 / LineBC / Fubon / ...」下拉，切換即時過濾
4. 遷移腳本跑完後，舊 `{project}/.ctx-save/context.db` 資料全數進入中央 DB，可選擇 archive 舊檔
5. CSRF 測試：跨 origin 的 POST `/api/saves/batch-delete` 回 403
6. XSS 測試：在 DB 手塞 `session_id = "abc');alert(1);//"`、`category = "<img src=x onerror=alert(1)>"`，viewer 不執行
