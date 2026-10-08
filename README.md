# Forge — Claude Code Plugin Marketplace

通用開發工具集，提供 Git 工作流、對話重點快照等實用 Plugin。

## 快速安裝

```bash
# 加入 Marketplace（只需一次）
claude plugin marketplace add mark22013333/forge

# 安裝需要的 Plugin
claude plugin install git-tools
claude plugin install ctx-save
claude plugin install ci-tools
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

### ci-tools — 自架 Azure DevOps Server 導入 CI

引導把自架 Azure DevOps Server 接上既有 Gitea 專案的 CI，附 pipeline 範本、Gradle init script、Gitea → ADO 同步腳本與實際導入時整理的踩坑表。

| Skill | 說明 | 觸發方式 |
|-------|------|---------|
| `ado-ci-onboard` | 從評估、安裝、匯入 repo、套範本到 REST API 驗證建置的導入流程 | 「導入 CI」「自架 Azure DevOps」「ADO Server」「Gitea 同步到 ADO」「pipeline 範本」 |

**功能特點**

- 24 條踩坑（症狀／根因／解法／驗證狀態），估計值與未釐清的項目照實標示
- `ci-init.gradle`：不改 build.gradle，關閉 failFast、每個測試類別獨立 JVM、只跑近期修改的測試（清單為空即失敗）
- Pipeline 範本：Gradle＋JDK17（流程已在作者環境實測；去識別化改寫版未重新執行），Maven JDK8／JDK25、Vue、Python（未驗證）
- `sync-gitea-to-ado.sh`：預設只預覽，`--apply` 才推；只快轉、分叉就停、保護分支只印指令
- 專案專屬值放在各專案的 `.ci-tools.yml`，不進 plugin

**同步腳本參數**

```
--gitea <url>        Gitea repo 網址（必填）
--ado <url>          ADO repo 網址（必填）
--branches <a,b>     只處理這些分支
--key <path>         SSH 私鑰（只用這把，不讀 ssh-agent）
--workdir <path>     本機工作 clone 路徑
--protected <a,b>    保護分支清單（預設 main,master,production）
--apply              實際推送（未帶時只預覽）
```

安裝：
```bash
claude plugin install ci-tools
```

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
