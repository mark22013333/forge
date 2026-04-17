# ctx-save — Context Save

> 在 Claude Code 自動壓縮前，保存對話重點。支援 Markdown 與 SQLite 儲存、PostToolUse Hook 自動提醒、Web Viewer 視覺化瀏覽。

**版本**：2.0.0
**技術棧**：Python 3 標準庫（無任何 pip 依賴）
**授權**：MIT

---

## 為什麼需要這個 Plugin？

Claude Code 在 context 使用率過高時會自動壓縮（compact），壓縮後許多細節會遺失。`ctx-save` 解決這個問題：

- **手動快照**：在關鍵節點用 `/ctx-save` 把當下對話重點儲存到本機
- **自動提醒**：context 超過閾值時（預設 80%）自動跳出提醒，避免被壓縮前忘了存
- **雙格式儲存**：每筆記錄同時寫 Markdown 檔 + SQLite（結構化查詢）
- **Web 視覺化**：不用開終端機，用瀏覽器瀏覽/搜尋/刪除/複製歷史記錄

---

## 安裝

### 前置條件

- Claude Code CLI
- Python 3.8+（macOS/Linux 皆內建）

### 安裝步驟

```bash
# 1. 加入 Marketplace（只需一次）
claude plugin marketplace add mark22013333/forge

# 2. 安裝 Plugin
claude plugin install ctx-save

# 3. 重啟 Claude Code 使 Plugin 生效
```

---

## 使用方式

### 手動儲存對話重點：`/ctx-save`

在 Claude Code 中輸入：

```
/ctx-save
```

觸發後，助手會：
1. 分析當前對話，整理出重點
2. 寫入 `.ctx-save/context.db`（SQLite）
3. 同時產出 `.ctx-save/snapshots/YYYY-MM-DD_HH-MM-SS.md`（Markdown 副本）

每筆記錄包含：`title`、`category`、`tags`、`content`、`context_percentage`、`session_id`、`created_at`。

### 啟動 Web Viewer：`/ctx-view`

```
/ctx-view
```

- 背景啟動 viewer（預設 `http://127.0.0.1:29898`，僅限本機）
- 若已有實例在跑 → 複用並開瀏覽器
- Port 衝突自動遞增（29898→29899→... 最多 10 個）
- 啟動後自動開預設瀏覽器

Web UI 功能：
- Session 列表（依時間排序、分類篩選、關鍵字搜尋）
- 每筆記錄：🗑 刪除、📋 複製內容、📋 複製 Markdown（含 frontmatter）
- Session 層級刪除（整個 session 一鍵清除）
- 批次刪除 Modal（依分類 + 日期範圍預覽後執行）
- 統計圖表（總筆數、session 數、分類分佈）

### 停止 Web Viewer：`/ctx-view-stop`

```
/ctx-view-stop
```

- 讀 PID file → 二次驗證 `/api/ping` 的 `service` 欄位
- SIGTERM 優雅停止（最多等 2 秒）→ SIGKILL 保底
- 清理 PID file

### 命令列工具：`ctx-db.py`

進階使用者可直接呼叫 DB 工具：

```bash
# 列出最近 10 筆
python3 ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py list 10

# 關鍵字搜尋
python3 .../ctx-db.py search "關鍵字"

# 依 session_id 查詢
python3 .../ctx-db.py get <session_id>

# 統計
python3 .../ctx-db.py stats

# 清理 N 天前的舊記錄
python3 .../ctx-db.py clean 30

# 手動執行 migration（新版首次使用自動執行，無需手動）
python3 .../ctx-db.py migrate
```

---

## 設定

### 環境變數

| 變數 | 預設 | 用途 |
|------|------|------|
| `CTX_VIEW_PORT` | `29898` | Web Viewer 起始探測 port |
| `CTX_VIEW_NO_OPEN` | 未設 | 設為 `1` 則 `/ctx-view` 不自動開瀏覽器 |

### 檔案位置

| 路徑 | 用途 |
|------|------|
| `.ctx-save/context.db` | SQLite 主檔（在專案根目錄建立） |
| `.ctx-save/snapshots/*.md` | Markdown 副本 |
| `~/.cache/ctx-viewer/viewer.pid` | Web Viewer PID file |
| `~/.cache/ctx-viewer/viewer.log` | Web Viewer 日誌 |

### 自動提醒 Hook（選配）

在 Claude Code 的 `settings.json` 加入 PostToolUse hook，context 超過 80% 時自動提示執行 `/ctx-save`：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh"
          }
        ]
      }
    ]
  }
}
```

詳細設定請見 `skills/ctx-save/references/hook-setup.md`。

---

## Skills 清單

| Skill | 類型 | 說明 |
|-------|------|------|
| `ctx-save` | 手動 | 在 Claude Code 中用 `/ctx-save` 觸發儲存 |
| `ctx-view` | User-invocable | `/ctx-view` 背景啟動 Web Viewer |
| `ctx-view-stop` | User-invocable | `/ctx-view-stop` 停止 Web Viewer |

---

## 更新

```bash
claude plugin update ctx-save@forge
```

首次從 v1 升級到 v2 時，`ctx-viewer.py` 會自動執行 schema migration（新增 `deleted_at` 欄位），冪等安全。

---

## Schema（v2.0）

```sql
CREATE TABLE context_saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    project_path TEXT NOT NULL,
    context_percentage REAL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    model TEXT DEFAULT '',
    deleted_at DATETIME  -- v2 新增，soft delete 標記
);
```

---

## 安全性

- **本機優先**：Web Viewer 綁定 `127.0.0.1`，不對外網開放
- **SQL 參數化**：全部 query 使用 parameterized statements
- **雙重驗證停止**：stopper 先讀 PID file，再透過 `/api/ping` 的 `service` 欄位確認是本服務才送訊號

---

## 疑難排解

**Port 全部被占用**
```
❌ 無可用 port，請釋放 29898-29907 範圍或設定 CTX_VIEW_PORT
```
→ 設定環境變數 `CTX_VIEW_PORT=<其他 port>` 後重試。

**找不到 context.db**
→ Web Viewer 會從當前目錄往上遞迴找 `.ctx-save/context.db`。若你從未執行過 `/ctx-save`，先存一筆記錄再啟動 viewer。

**Viewer 進程卡死**
```bash
# 強制清理
pkill -f ctx-viewer.py
rm ~/.cache/ctx-viewer/viewer.pid
```

---

## 授權

MIT License — 詳見專案根目錄 LICENSE。
