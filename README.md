# Forge — Claude Code Plugin Marketplace

通用開發工具集，提供 Git 工作流、對話重點快照等實用 Plugin。

## 快速安裝

```bash
# 加入 Marketplace（只需一次）
claude plugin marketplace add mark22013333/forge

# 安裝需要的 Plugin
claude plugin install git-tools
claude plugin install ctx-save
```

安裝後**重啟 Claude Code** 使 Plugin 生效。

---

## Plugin 一覽

### Git Tools

自動分析 git diff，產出 Conventional Commits 格式的 Commit Message、Branch Name、Git Label 與版本公告。

| Skill | 說明 | 觸發方式 |
|-------|------|---------|
| `diff-summary` | 分析 git diff 產出完整 Commit Message | 「分析 diff」「產生 commit message」「寫 commit」「變更摘要」 |

**功能特點**

- 自動過濾設定檔（application-*.yml、pom.xml、*.properties）
- 大型 diff（>3000 行）自動切換逐檔分析模式
- 多功能變更自動建議拆分成多個 Commit
- 同時產出繁體中文與英文版本
- 附帶版本公告 JSON，供非技術人員閱讀

**支援參數**

```
--cached           只分析已 staged 的變更
--branch <name>    與指定分支比較
```

安裝：
```bash
claude plugin install git-tools
```

---

### ctx-save — 對話重點快照 + Web Viewer

在 Claude Code 自動壓縮前保存對話重點。支援 Markdown 與 SQLite 儲存、PostToolUse Hook 自動提醒、Web Viewer 視覺化瀏覽。

| Skill | 類型 | 說明 |
|-------|------|------|
| `ctx-save` | 手動 | `/ctx-save` 把當前對話重點寫入 SQLite + Markdown |
| `ctx-view` | User-invocable | `/ctx-view` 背景啟動 Web Viewer（預設 `http://127.0.0.1:29898`） |
| `ctx-view-stop` | User-invocable | `/ctx-view-stop` 優雅停止 Web Viewer |

**功能特點**

- 純 Python 標準庫，無任何 pip 依賴
- Web UI：瀏覽 / 搜尋 / 刪除 / 複製（內容 & 含 frontmatter 的 Markdown）
- 批次刪除 Modal（依分類 + 日期範圍預覽後執行）
- Port 衝突自動遞增 + lsof 診斷占用者
- Server 重用判斷：PID file + `/api/ping` 雙保險
- Context 超過閾值時 PostToolUse hook 自動提醒

安裝：
```bash
claude plugin install ctx-save
```

詳細使用方式：[plugins/ctx-save/README.md](plugins/ctx-save/README.md)

---

## 更新

```bash
claude plugin update <plugin-name>@forge
```

---

## 開發者：版本管理

**單一真相來源**：每個 `plugins/<name>/.claude-plugin/plugin.json` 的 `version` 欄位。`marketplace.json` 內對應的 `version` 是派生值。

升版流程：

```bash
# 1. 修改對應 plugin.json 的 version
# 2. 同步到 marketplace.json
python3 scripts/sync-versions.py

# 3. commit（pre-commit hook 會再驗證一次）
git add . && git commit -m "chore(ctx-save): 升版 2.3.2"
```

首次 clone 後啟用 hook：

```bash
git config core.hooksPath .githooks
```

驗證（CI / 手動）：

```bash
python3 scripts/sync-versions.py --check  # 不一致 → exit 1
```

---

## 授權

MIT License
