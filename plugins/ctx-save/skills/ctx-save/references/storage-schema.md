# Context Save 儲存格式

## Markdown 檔案格式

### 路徑規則

```
{project_root}/.ctx-save/sessions/{YYYY-MM-DD_HHmm}-{slug}.md
```

slug 由快照標題產生：取前 40 字元、空白轉 `-`、移除特殊字元、轉小寫。

### 檔案範本

```markdown
---
session_id: abc-123-def
project: /Users/cheng/IdeaProjects/Taipei/LineBC
context_percentage: 72
model: claude-opus-4-6
created_at: 2026-04-17T15:30:00+08:00
item_count: 4
categories:
  - task
  - finding
  - decision
  - insight
---

# 對話快照：PushService 重構進度

## 📋 任務進度
- 正在重構 PushService 錯誤處理
- 已完成：filter 層的 input validation、validate 層的 status 檢查
- 剩餘：retry 邏輯（指數退避）、單元測試撰寫
- 相關檔案：`PushService.java`, `PushBySchedule.java`

## 🔍 發現與結論
- `msg_main` 表的 `status` 欄位有 4 種未文件化的狀態碼
  - 0=待發送, 1=已發送, 2=失敗, 9=已取消
  - 只有 0 和 1 在程式碼中有常數定義，2 和 9 是硬編碼

## 🏗️ 架構決策
- 推播重試採用指數退避（1s → 2s → 4s），最多 3 次
  - 理由：固定間隔在 LINE API 限流時會連續失敗
  - 替代方案：jitter + exponential backoff（暫不需要，併發量不高）

## 💡 重要洞察
- `Interceptor.excludeUrls` 用 `startsWith` 比對
  - `/API/push` 和 `/API/pushCallback` 都會被 `/API/push` 匹配到
  - 這是設計還是 bug 需要確認
```

### 類別對照表

| 類別 key | 圖示 | 中文名稱 |
|----------|------|---------|
| task | 📋 | 任務進度 |
| finding | 🔍 | 發現與結論 |
| decision | 🏗️ | 架構決策 |
| bug | 🐛 | Bug 追蹤 |
| change | 📝 | 程式碼變更 |
| insight | 💡 | 重要洞察 |
| reference | 🔗 | 外部資源 |
| custom | 📌 | 自訂 |

---

## SQLite Schema

### 資料表：context_saves

```sql
CREATE TABLE context_saves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,           -- Claude Code session ID
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    project_path        TEXT NOT NULL,           -- 專案根目錄絕對路徑
    context_percentage  REAL,                    -- 儲存時的 context 使用率
    title               TEXT NOT NULL,           -- 項目標題（一行摘要）
    category            TEXT NOT NULL,           -- 類別 key（task/finding/decision/...）
    content             TEXT NOT NULL,           -- 完整內容（Markdown 格式）
    tags                TEXT DEFAULT '',         -- 逗號分隔的標籤
    model               TEXT DEFAULT ''          -- 使用的模型 ID
);
```

### 索引

```sql
CREATE INDEX idx_saves_session  ON context_saves(session_id);
CREATE INDEX idx_saves_project  ON context_saves(project_path);
CREATE INDEX idx_saves_created  ON context_saves(created_at DESC);
CREATE INDEX idx_saves_category ON context_saves(category);
```

### 操作範例

```bash
# 儲存（透過 stdin 傳入 JSON）
echo '[
  {
    "session_id": "abc-123",
    "project_path": "/Users/cheng/IdeaProjects/Taipei/LineBC",
    "context_percentage": 72,
    "title": "PushService 重構 — 已完成 filter/validate 層",
    "category": "task",
    "content": "正在重構 PushService...",
    "tags": "refactor,push",
    "model": "claude-opus-4-6"
  }
]' | CTX_PROJECT=/Users/cheng/IdeaProjects/Taipei/LineBC python3 ctx-db.py save

# 列出快照
CTX_PROJECT=/path/to/project python3 ctx-db.py list

# 取得特定 session
python3 ctx-db.py get abc-123

# 搜尋
CTX_PROJECT=/path/to/project python3 ctx-db.py search "PushService"

# 清除 30 天前的記錄
python3 ctx-db.py clean 30

# 統計
python3 ctx-db.py stats
```

---

## .gitignore 條目

自動保存後需確認 `.gitignore` 包含：

```
.ctx-save/
```
