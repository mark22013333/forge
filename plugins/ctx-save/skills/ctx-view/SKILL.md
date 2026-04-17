---
name: ctx-view
description: 啟動 ctx-save SQLite 記錄的 Web 視覺化介面（背景執行，自動開瀏覽器）
user_invocable: true
---

# ctx-view — 啟動 Web Viewer

啟動 `ctx-save` 儲存的 SQLite 記錄（`.ctx-save/context.db`）對應的 Web 視覺化介面。Server 以背景程序執行，啟動完成後自動開啟預設瀏覽器。

## 使用方式

**直接執行以下命令**（不要改寫、不要增加額外參數）：

```bash
python3 "$HOME/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-view/scripts/ctx-view-launcher.py"
```

執行完成後，將命令輸出（URL、PID、DB 路徑）完整呈現給使用者。

## 行為

1. **定位 DB**：從當前工作目錄往上遞迴尋找 `.ctx-save/context.db`；找不到會印錯誤並以非 0 退出。
2. **複用判斷**：若已有本服務在跑（PID file + `/api/ping` 驗證通過），印「♻ 重用既有 viewer」後開瀏覽器並退出。
3. **Port 選擇**：從 `CTX_VIEW_PORT`（預設 29898）起遞增探測，最多嘗試 10 個 port。衝突時會透過 `lsof` 提示占用者 PID/程序名。
4. **背景啟動**：以 `subprocess.Popen(start_new_session=True)` 啟動 `ctx-viewer.py`，stdout/stderr 重導至 `~/.cache/ctx-viewer/viewer.log`。
5. **驗證 + 寫 PID**：輪詢 `/api/ping` 最多 3 秒；通過後寫入 `~/.cache/ctx-viewer/viewer.pid`。

## 環境變數

| 變數 | 預設 | 用途 |
|------|------|------|
| `CTX_VIEW_PORT` | `29898` | 起始探測 port |
| `CTX_VIEW_NO_OPEN` | 未設 | 設為 `1` 則不自動開啟瀏覽器 |

## 相關

- 停止服務：`/ctx-view-stop`
- 儲存記錄：`/ctx-save`
- 日誌位置：`~/.cache/ctx-viewer/viewer.log`
- PID file：`~/.cache/ctx-viewer/viewer.pid`
