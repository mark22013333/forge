---
name: ctx-view-stop
description: 停止由 /ctx-view 啟動的背景 Web Viewer 服務
user_invocable: true
---

# ctx-view-stop — 停止 Web Viewer

停止由 `/ctx-view` 啟動的背景 `ctx-viewer` 服務。會做雙重驗證（PID 存活 + `/api/ping` 服務身份）以避免誤殺其他程序。

## 使用方式

**直接執行以下命令**（不要改寫、不要傳入 PID 等任何參數）：

```bash
python3 "$HOME/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-view-stop/scripts/ctx-view-stop.py"
```

執行完成後，將命令輸出完整呈現給使用者。

## 行為

1. **讀 PID file**：`~/.cache/ctx-viewer/viewer.pid`。不存在 → 印「ℹ Viewer 未執行中」退出 0。
2. **存活檢查**：`os.kill(pid, 0)`。已死 → 清理 stale PID file 後退出 0。
3. **服務驗證**：GET `/api/ping` 必須回 `service == "ctx-viewer"`。否則拒絕停止並以非 0 退出。
4. **SIGTERM 優先**：等待最多 2 秒。
5. **SIGKILL 保底**：仍存活時強制終止。
6. **清理**：刪除 PID file 並印「✅ Viewer 已停止」。

## 退出碼

| 退出碼 | 情況 |
|--------|------|
| 0 | 成功停止、或本就未執行、或已 stale |
| 2 | PID 非本服務，拒絕操作 |
| 3 | 無法發送訊號（權限不足等） |
| 130 | 被使用者中斷（Ctrl+C） |

## 相關

- 啟動服務：`/ctx-view`
