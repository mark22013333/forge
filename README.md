# Forge — Claude Code Plugin Marketplace

通用開發工具集，提供 Git 工作流、程式碼分析等實用 Skill。

## 快速安裝

```bash
# 加入 Marketplace
claude plugin marketplace add mark22013333/forge

# 安裝 Plugin
claude plugin install git-tools
```

安裝後**重啟 Claude Code** 使 Plugin 生效。

---

## Plugin 一覽

### Git Tools

自動分析 git diff，產出 Conventional Commits 格式的 Commit Message、Branch Name、Git Label 與版本公告。

| Skill | 說明 | 觸發方式 |
|-------|------|---------|
| `diff-summary` | 分析 git diff 產出完整 Commit Message | 「分析 diff」「產生 commit message」「寫 commit」「變更摘要」 |

**功能特點：**

- 自動過濾設定檔（application-*.yml、pom.xml、*.properties）
- 大型 diff（>3000 行）自動切換逐檔分析模式
- 多功能變更自動建議拆分成多個 Commit
- 同時產出繁體中文與英文版本
- 附帶版本公告 JSON，供非技術人員閱讀

**支援參數：**

```
--cached           只分析已 staged 的變更
--branch <name>    與指定分支比較
```

---

## 更新

```bash
claude plugin update git-tools@forge
```

---

## 授權

MIT License
