---
slug: ctx-save-v3
type: feature
name: ctx-save v3 功能規格（閉環 / 診斷 / 管理 UI）
status: 已完成
branch: —
notion: —
created: 2026-04-21
---

# ctx-save v3

## 一句話

補齊 ctx-save v2.3 的核心缺口：**compact 後沒有「自動把重點注回對話」的閉環**；同時加上對話健康診斷（token 分類 + 浪費偵測）與 Web 管理儀表板。

## 背景

- 既有專案：`plugins/ctx-save`（Claude Code plugin，Python 標準庫、零 pip 依賴）
- 當前版本：**v2.3.1**
- 已能做：手動 `/ctx-save`、PreCompact auto-dump、PostToolUse alert、中央 SQLite DB、Web Viewer（單頁列表）、三種模式 off/assist/auto、`/ctx-mode` 切換
- 關鍵缺口：PreCompact 已寫 snapshot 進 DB，但 **SessionStart 沒有 `matcher="compact"` 分支**，compact 結束後 Claude 不知道剛存了什麼，使用者仍需手動 `/ctx-save restore`

## 要做什麼（v3 三條主軸）

1. **閉環**：SessionStart `matcher="compact"` 自動把最新 snapshot 摘要透過 stderr 注回對話
2. **診斷**：新增 `/ctx-save analyze` — 讀 transcript JSONL，做五類 token 分類、六條浪費規則偵測，產生 `/compact focus on <files>` 可貼指令（對標 claude-crusts，但嚴守 Python 標準庫）
3. **管理 UI**：Web Viewer 從「歷史瀏覽器」升級為「對話健康儀表板」— Timeline / Dashboard / Diagnostics / Persistent 四個 tab

## 不做什麼

- 不引入 Obsidian / Graphify / 任何 vault
- 不接 OpenAI / Anthropic / Slack / Email 等外部服務
- 不加任何 pip 依賴（Python stdlib only）
- 不破壞「一鍵安裝、馬上能用」的體驗
- 不做使用者自訂規則（plugin-like 介面，超出 v3 範圍）

## 參考來源

四個 GitHub repo 各取一角度（完整「借 / 不借」清單見 spec.md §2）：

| Repo | 借鑑角度 |
|------|---------|
| Abinesh-L/claude-crusts | 診斷：token 分類、浪費偵測、focus hint |
| lucasrosati/claude-code-memory-setup | 減量（僅觀念，不引入 Obsidian） |
| mcpware/claude-code-organizer | Web Dashboard scan → find → fix 哲學 |
| VoxCore84/claude-code-compaction-keeper | Two-stage 自動還原閉環模型 |

## 衍生文件

- [spec.md](./spec.md) — 完整技術規格（11 章 + 2 附錄）
- [db.md](./db.md) — 資料庫設計（schema 變更、migration、索引）
- [db.sql](./db.sql) — 可執行 SQL（Section A 全新安裝 / B migration / C 範例 / D rollback）
- [arch.md](./arch.md) — 架構設計（Mermaid 圖、類別清單、介面定義、設計模式）
- [files.md](./files.md) — 程式碼產出清單（35 個檔案 / 10,164 行）
- [review.md](./review.md) — 程式碼審查報告（邏輯 / 品質 / 安全三人審）
- [log.md](./log.md) — 開發日誌

## 里程碑概覽

| M | 重點 | 預估 |
|---|------|------|
| M1 | 閉環核心（SessionStart re-inject + N8 events CLI） | 2-3 週 |
| M2 | 診斷基礎（analyze + 4 層防噪） | 2 週 |
| M3 | Web Viewer 四 tab 升級 | 2 週 |
| M4 | Distill / Persistent / compact_events 表 | 2 週 |
| M5 | 打磨、遷移驗證、文件 | 1 週 |

## 相關任務

- `centralized-db` (2026-04-19 完成) — v2.3 的 SQLite 集中式儲存
