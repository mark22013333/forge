#!/bin/bash
# ctx-alert.sh — PostToolUse hook：Context 使用率超過閾值時提醒使用者
#
# 功能：
#   - 讀取 statusline JSON 取得 context 使用率
#   - 超過閾值時輸出提醒訊息
#   - 內建冷卻機制，避免每次 tool call 都重複提醒
#
# 安裝方式見 references/hook-setup.md

STATUSLINE="/tmp/claude/statusline-last-input.json"
THRESHOLD="${CTX_ALERT_THRESHOLD:-70}"
COOLDOWN_FILE="/tmp/claude/ctx-alert-cooldown"
COOLDOWN_SECONDS="${CTX_ALERT_COOLDOWN:-300}"  # 預設 5 分鐘

# 冷卻檢查：避免頻繁提醒
if [ -f "$COOLDOWN_FILE" ]; then
    LAST=$(cat "$COOLDOWN_FILE" 2>/dev/null)
    NOW=$(date +%s)
    DIFF=$((NOW - LAST))
    if [ "$DIFF" -lt "$COOLDOWN_SECONDS" ]; then
        exit 0
    fi
fi

# statusline 檔案不存在則靜默退出
if [ ! -f "$STATUSLINE" ]; then
    exit 0
fi

# 讀取 context 使用率
USED=$(python3 -c "
import json, sys
try:
    with open('$STATUSLINE') as f:
        data = json.load(f)
    pct = data.get('context_window', {}).get('used_percentage', 0)
    print(int(pct))
except Exception:
    print(0)
" 2>/dev/null)

# 未取得數值則退出
if [ -z "$USED" ] || [ "$USED" = "0" ]; then
    exit 0
fi

# 超過閾值：輸出提醒並記錄冷卻時間
if [ "$USED" -ge "$THRESHOLD" ] 2>/dev/null; then
    echo "⚠️ Context 使用率已達 ${USED}%（閾值 ${THRESHOLD}%），建議執行 /ctx-save 保存重要對話資訊"
    date +%s > "$COOLDOWN_FILE"
fi
