---
name: ctx-mode
description: 切換 ctx-save 自動觸發模式與閾值（off / assist / auto）— 手機遠端操作首選
user_invocable: true
---

# ctx-mode — 觸發模式切換

一行切換 ctx-save 的觸發模式，免去手敲 `python3 ...ctx-config.py set mode auto` 長指令。

## 使用方式

將使用者傳入的 `$ARGUMENTS` 附加到下列指令後**直接執行**：

```bash
python3 "$HOME/.claude-company/plugins/marketplaces/forge/plugins/ctx-save/scripts/ctx-mode.py" $ARGUMENTS
```

執行完成後，把 stdout 原文呈現給使用者（已是繁體中文的人類可讀輸出）。

## 支援的參數

| 指令 | 效果 |
|------|------|
| `/ctx-mode` | 顯示當前配置 |
| `/ctx-mode off` | 關閉提醒與自動 dump（PreCompact 保底仍會生效） |
| `/ctx-mode assist` | 只提醒，不自動 dump（預設） |
| `/ctx-mode auto` | 提醒 + 超過 dump 閾值自動 raw dump |
| `/ctx-mode alert <N>` | 改提醒閾值百分比（1-99） |
| `/ctx-mode dump <N>` | 改自動 dump 閾值百分比（1-99） |
| `/ctx-mode cooldown <N>` | 改冷卻分鐘數（>=0） |
| `/ctx-mode tail <KB>` | 改 transcript 尾端讀取 KB 數（>=1） |

## 何時用哪個模式？

- **`off`**：不想被提醒（手動 `/ctx-save` 為主），但仍要 PreCompact 保底。
- **`assist`**：日常使用。context 到閾值時提醒你手動 `/ctx-save`。
- **`auto`**：手機/遠端控制，沒人能及時手動存。超閾值自動 raw dump 到 SQLite。

## 重要觀念（使用者常問）

**「會不會自動 dump？」** — 只有 `auto` 模式才會主動 dump。`off` / `assist` 都不會。但 Claude Code 壓縮對話前仍會觸發 **PreCompact 保底 dump**（不受 mode 影響）。

**「冷卻時間是什麼？」** — 兩次提醒/主動 dump 的節流間隔（預設 15 分鐘），避免連續洗頻。PreCompact 保底 dump 與手動 `/ctx-save` 不受冷卻限制。

## 欄位速查

| 欄位 | 預設 | 說明 |
|------|------|------|
| `alert_threshold` | 60 | 達此 % 觸發提醒 |
| `auto_dump_threshold` | 70 | `auto` 模式下達此 % 觸發主動 dump（CC 約 75% 自動 compact，務必 < 75） |
| `cooldown_minutes` | 15 | 提醒/主動 dump 節流間隔（分鐘，>=0） |
| `transcript_tail_kb` | 200 | dump 時讀 transcript 尾端多少 KB |

## 範例互動

```
使用者：/ctx-mode auto
助手：✅ 模式已切換為 auto
      📋 ctx-save 配置
         模式         : 🤖 auto（提醒 + 自動 dump）
         提醒閾值     : 60%
         自動 dump    : 70%
         ...
```

## 相關

- 儲存記錄：`/ctx-save`
- 瀏覽記錄：`/ctx-view`
- 底層配置：`~/.ctx-save/config.json`
