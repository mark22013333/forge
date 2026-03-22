---
name: diff-summary
description: 分析 git diff 並產出 Conventional Commits 格式的 Commit Message。當使用者提到「diff-summary」、「分析 diff」、「產生 commit message」、「寫 commit」、「變更摘要」、「code change summary」時觸發此 Skill。
---

# Diff Summary — Git 變更分析與 Commit Message 產生器

分析當前 Git 工作目錄的變更內容，產出符合 Conventional Commits 標準的 Commit Message、建議 Branch Name、Git Label，以及使用者公告 JSON。

---

## 執行流程

### 1. 收集變更資訊

依序執行以下指令收集差異資料：

```bash
# 概覽：哪些檔案改了、改了多少行
git diff HEAD --stat

# 已 staged 的變更概覽
git diff --cached --stat

# 詳細 diff，排除設定檔
git diff HEAD -- ':!**/application-*.yml' ':!**/application.yml' ':!*pom.xml' ':!**/*.properties'
```

若使用者帶入 `$ARGUMENTS`，按以下規則調整：

| 參數 | 行為 |
|------|------|
| `--cached` | 只分析已 staged 的變更 |
| `--branch <name>` | 改用 `git diff <name>...HEAD` 比較指定分支 |
| 無參數 | 預設分析所有未提交變更（含 staged + unstaged） |

### 2. 大型 Diff 處理策略

當 diff 輸出超過 3,000 行時，切換為逐檔分析模式：

1. 從 `--stat` 結果列出所有變更檔案清單
2. 對每個檔案個別執行 `git diff HEAD -- <filepath>` 並摘要
3. 彙整所有摘要後產出結果

### 3. 過濾規則

**完全忽略以下檔案的變更，不寫入 Commit Message：**
- `application-*.yml`、`application.yml`（Spring 設定檔）
- `pom.xml`（Maven 設定檔）
- `*.properties`（環境設定檔）

**專注分析：** Java、XML、SQL、JSP、JavaScript、HTML、CSS 等程式碼的邏輯變更。

### 4. 分析與分類

閱讀每個檔案的變更，識別：
- **改了什麼**：具體的類別、方法、欄位變更
- **為什麼改**：從程式碼上下文推斷修改動機
- **影響範圍**：屬於哪個模組或功能區塊

避免籠統敘述（如「修改程式碼」），應寫出具體內容（如「修正 WCS120 欄位長度檢核邏輯」）。

### 5. 產出結果

產出四個部分，全部包裹在一個 ``` 程式碼區塊中。格式規範與完整範例參照 `references/commit-format.md`。

**摘要：**

1. **繁體中文 Commit Message** — `<type>(<scope>): <subject>` + Body
2. **英文 Commit Message** — 翻譯中文版本
3. **建議 Branch Name** — kebab-case 格式
4. **Git Label** — 從標準清單選擇

### 6. 版本公告（JSON）

在 Commit Message 區塊之後，另外產出 JSON 格式的版本公告。以非技術人員能理解的語言描述變更。格式參照 `references/announcement-format.md`。

### 7. 多功能變更處理

若偵測到多個不相關的功能變更混在一起，主動建議拆分成多個 Commit Message，並分別產出各自的完整結果。

---

## 邊界情況

| 情境 | 處理方式 |
|------|---------|
| 只有 yml/pom/properties 變更 | 回報「無程式碼邏輯變更，僅設定檔異動」 |
| 沒有任何變更 | 回報「工作目錄乾淨，無待分析的變更」 |
| 變更跨多個不相關模組 | 建議拆分，分別產出 Commit Message |

---

## 參考資源

### 格式規範

- **`references/commit-format.md`** — Conventional Commits 撰寫規範、Type 定義、完整輸出範例
- **`references/announcement-format.md`** — 版本公告 JSON 格式規範與範例
