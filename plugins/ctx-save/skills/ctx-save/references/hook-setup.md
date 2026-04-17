# Context Alert Hook 安裝指南

## 概述

`ctx-alert.sh` 是一個 PostToolUse hook 腳本，在每次工具呼叫後檢查 context window 使用率。超過閾值時輸出提醒訊息，建議使用者執行 `/ctx-save`。

內建 5 分鐘冷卻機制，不會每次 tool call 都提醒。

## 安裝方式

### 方式一：透過 /update-config skill

在 Claude Code 中執行：

```
/update-config 新增一個 PostToolUse hook，命令為 bash ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh，不過濾特定工具
```

### 方式二：手動編輯 settings.json

編輯 `~/.claude-company/settings.json`（公司環境）或 `~/.claude/settings.json`（個人環境），在 `hooks` 區段新增：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "bash ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh"
      }
    ]
  }
}
```

若已有其他 PostToolUse hook，將新物件加入陣列即可。

### 方式三：專案層級設定

在專案根目錄的 `.claude/settings.local.json` 新增，僅在該專案生效：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "bash ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh"
      }
    ]
  }
}
```

## 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `CTX_ALERT_THRESHOLD` | `70` | 使用率閾值（%），超過才提醒 |
| `CTX_ALERT_COOLDOWN` | `300` | 冷卻時間（秒），兩次提醒的最小間隔 |

### 自訂閾值範例

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "CTX_ALERT_THRESHOLD=65 CTX_ALERT_COOLDOWN=600 bash ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh"
      }
    ]
  }
}
```

上面的設定：65% 就開始提醒，冷卻 10 分鐘。

## 運作原理

```
Tool Call 完成
    ↓
PostToolUse hook 觸發
    ↓
ctx-alert.sh 執行
    ↓
檢查冷卻 → 未過冷卻期 → 靜默退出
    ↓（已過冷卻期）
讀取 /tmp/claude/statusline-last-input.json
    ↓
解析 context_window.used_percentage
    ↓
< 閾值 → 靜默退出
≥ 閾值 → 輸出提醒 + 記錄冷卻時間
```

## 效能影響

- 腳本極為輕量：一次 file read + 一次 python JSON parse
- 冷卻機制確保大部分呼叫在第一步就退出（只讀一個小檔案）
- 不會明顯影響 tool call 速度

## 驗證安裝

```bash
# 確認腳本可執行
chmod +x ~/.claude-company/skills/ctx-save/scripts/ctx-alert.sh

# 手動測試
bash ~/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/skills/ctx-save/scripts/ctx-alert.sh
# 若 context > 70% 會看到提醒訊息，否則無輸出
```

## 移除

從 settings.json 的 `hooks.PostToolUse` 陣列中移除對應的物件即可。
