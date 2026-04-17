---
name: ctx-save
description: This skill should be used when the user asks to "save context", "ctx save", "保存上下文", "儲存對話重點", "context snapshot", or wants to preserve important conversation information before automatic compaction. Also triggered by "restore context", "ctx restore", "載入上下文", "ctx list", "ctx clean", "ctx search".
user_invocable: true
---

# Context Save — 對話重點快照

在 Claude Code 自動壓縮（compact）之前，主動保存對話中的重要資訊。由使用者決定哪些值得留下，避免 AI 自動壓縮遺漏關鍵資訊。

## 運作模式

根據參數決定模式：

| 呼叫方式 | 模式 | 說明 |
|---------|------|------|
| `/ctx-save` | save | 分析對話 → 列出選項 → 使用者多選 → 儲存 |
| `/ctx-save restore` | restore | 列出已保存快照 → 使用者選擇 → 載入到對話 |
| `/ctx-save list` | list | 列出所有快照摘要 |
| `/ctx-save search <keyword>` | search | 搜尋歷史快照 |
| `/ctx-save clean [N]` | clean | 清除 N 天前的快照（預設 30） |
| `/ctx-save view` | view | 啟動 Web UI 視覺化瀏覽 SQLite 記錄 |

---

## Save 模式（預設）

### 步驟 1：檢查 Context 使用率

讀取 `/tmp/claude/statusline-last-input.json`，顯示：

```
Context 使用率: {used_percentage}% ({input_tokens} / {context_window_size} tokens)
```

若低於 50%，提示壓力不大但仍可保存。

### 步驟 2：分析對話並分類

回顧對話內容，將有價值的資訊歸入以下類別（跳過空類別）：

| 類別 | 圖示 | 包含內容 |
|------|------|---------|
| 任務進度 | 📋 | 進行中/已完成的步驟、剩餘工作 |
| 發現與結論 | 🔍 | 調查結果、根因分析、效能數據 |
| 架構決策 | 🏗️ | 設計選擇、取捨理由、替代方案 |
| Bug 追蹤 | 🐛 | 發現的問題、重現步驟、修復方式 |
| 程式碼變更 | 📝 | 修改了哪些檔案、關鍵改動摘要 |
| 重要洞察 | 💡 | 非顯而易見的發現、陷阱、注意事項 |
| 外部資源 | 🔗 | 參考文件、API 連結、相關 Issue |
| 自訂 | 📌 | 不屬於以上類別的重要內容 |

每個項目以**一行摘要**呈現，搭配編號。每個摘要應包含足夠上下文，讓未來的 session 能理解其含義。

### 步驟 3：列出選項並等待選擇

範例呈現格式：

```
## 對話重點快照

Context: 72% used (145k / 200k tokens)

  1. [📋 任務] 正在重構 PushService 錯誤處理，已完成 filter/validate 層，剩 retry 邏輯
  2. [🔍 發現] msg_main.status 有 4 種未文件化狀態碼：0=待發送 1=已發送 2=失敗 9=取消
  3. [🏗️ 決策] 推播重試採用指數退避（1s/2s/4s），最多 3 次，非固定間隔
  4. [🐛 Bug] RichmenuSchedule NPE — richmenu_period 無資料時未檢查 null
  5. [📝 變更] PushService.java +89 -34, PushBySchedule.java +12 -5
  6. [💡 洞察] Interceptor.excludeUrls 用 startsWith 比對，路徑多 / 就繞過

請選擇要保存的項目（逗號分隔如 1,3,5 或 all）：
```

使用 AskUserQuestion 等待選擇。

### 步驟 4：選擇儲存方式

```
儲存方式：
  [1] 📄 Markdown 檔案（.ctx-save/sessions/）
  [2] 🗄️ SQLite 資料庫（.ctx-save/context.db）
預設 [1]：
```

若專案已有 `.ctx-save/context.db` 則預設 [2]。使用 AskUserQuestion 等待。

### 步驟 5：寫入

**Markdown**：寫入 `{project}/.ctx-save/sessions/{YYYY-MM-DD_HHmm}-{slug}.md`，slug 由標題自動產生。格式見 `references/storage-schema.md`。

**SQLite**：執行 `python3 {skill_dir}/scripts/ctx-db.py save`，透過 stdin 傳入 JSON。環境變數 `CTX_PROJECT` 設為專案根目錄。

寫入後檢查 `.ctx-save/` 是否已在 `.gitignore`，若無則追加。

---

## Restore 模式

1. 偵測儲存類型（掃描 `.ctx-save/sessions/` 和 `.ctx-save/context.db`）
2. 列出可用快照（日期、標題、類別數、context%）
3. AskUserQuestion 等待選擇
4. 讀取內容並以格式化 markdown 輸出到對話

---

## List 模式

以表格顯示所有快照摘要：日期、標題、類別圖示、context%、項目數。

## Search 模式

以關鍵字搜尋歷史快照。Markdown 模式用 grep，SQLite 模式用 `scripts/ctx-db.py search`。

## Clean 模式

刪除 N 天前的快照。先列出待刪數量，使用 AskUserQuestion 確認。

---

## 儲存路徑

```
{project_root}/
└── .ctx-save/
    ├── sessions/           # Markdown 快照
    │   ├── 2026-04-17_1530-push-service-refactor.md
    │   └── 2026-04-16_1015-richmenu-bug-fix.md
    └── context.db          # SQLite（可選）
```

---

## View 模式（Web UI）

啟動本地 HTTP Server 視覺化瀏覽 SQLite 記錄：

```bash
python3 {skill_dir}/scripts/ctx-viewer.py --open
```

參數說明：
- `--db <path>`：指定 context.db 路徑（預設自動搜尋當前目錄往上）
- `--port <N>`：指定 port（預設 29898）
- `--open`：啟動後自動開啟瀏覽器

功能：Session 列表、詳情檢視、分類篩選、關鍵字搜尋、統計圖表。純 Python 標準庫，無需安裝任何套件。

執行此模式時，用 Bash 工具在背景啟動 server，然後告知使用者 URL。

---

## 搭配 Hook 自動提醒

可搭配 PostToolUse hook 在 context 超過閾值時自動提醒。安裝與設定方式見 `references/hook-setup.md`。

## Additional Resources

### Reference Files

- **`references/storage-schema.md`** — Markdown 與 SQLite 儲存格式定義
- **`references/hook-setup.md`** — PostToolUse hook 安裝與設定說明

### Scripts

- **`scripts/ctx-db.py`** — SQLite 資料庫操作（init / save / list / get / search / clean）
- **`scripts/ctx-alert.sh`** — PostToolUse hook 提醒腳本（含冷卻機制）
- **`scripts/ctx-viewer.py`** — Web UI 視覺化伺服器（純 Python 標準庫，port 29898）
