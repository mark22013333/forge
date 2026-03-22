# Conventional Commits 撰寫規範

## 格式

```
<type>(<scope>): <subject>

- <詳細說明 1>
- <詳細說明 2>
```

---

## Type 定義

| Type | 用途 | 範例 |
|------|------|------|
| `feat` | 新增功能 | 新增自動綁定追蹤功能 |
| `fix` | 修復 Bug | 修正欄位長度檢核邏輯 |
| `refactor` | 重構（不改變行為） | 抽取共用驗證邏輯為獨立 Service |
| `perf` | 效能優化 | 優化批次查詢減少 N+1 問題 |
| `style` | 格式調整（不影響邏輯） | 統一縮排與 import 排序 |
| `test` | 測試相關 | 新增 UserService 單元測試 |
| `docs` | 文件變更 | 更新 API 文件說明 |
| `chore` | 雜項（建置、工具） | 更新 CI 腳本 |

---

## Scope 規則

- 若能識別模組或功能區塊，加上 scope（如 `push`、`user-trace`、`richmenu`）
- 無法明確歸屬時省略 scope
- 使用 kebab-case

---

## Subject 規則

- 簡潔明瞭的一句話
- 不加句號
- 使用繁體中文（台灣軟體開發慣用語）

---

## Body 規則

- 使用「-」符號逐項列出修改點
- 具體說明「改了什麼」與「為什麼改」
- 避免籠統敘述
- **純文字**：不使用 Markdown 格式（粗體 `**text**`、斜體、引用 `>`）
- **不使用編號**（`1.` `2.`），統一用「-」
- 不使用 `[cite]`、`[cite_start]` 等標記

---

## Git Label 選項

從以下清單選擇一個最合適的：

| Label | 使用時機 |
|-------|---------|
| `bug` | 修復 Bug |
| `enhancement` | 新功能或功能增強 |
| `documentation` | 文件變更 |
| `duplicate` | 重複的變更 |
| `good first issue` | 簡單的入門修改 |
| `help wanted` | 需要協助的變更 |
| `invalid` | 無效變更 |
| `question` | 需要討論的變更 |
| `wontfix` | 不會修復 |

---

## Branch Name 規則

- 使用 kebab-case
- 格式：`<type>/<簡短描述>`
- 範例：`fix/wcs120-column-issue`、`feat/auto-bind-tracking`

---

## 完整輸出範例

以下為一個完整的輸出結構，所有內容包裹在一個程式碼區塊中：

```
繁體中文 Commit Message:
feat(user-trace): 新增自動綁定追蹤與優化使用者狀態查詢邏輯

- 在 UpdateBindingStatusService 與 ReceivingMsgHandlerMsgReceiveOp 中新增 ACTION_AutoBinded 紀錄，區分一般綁定與自動綁定行為
- 在 ReceivingMsgHandlerMsgReceiveOp 新增全域 Unbind 行為紀錄
- LineUserService 新增 findByMidForTracking 方法，專供追蹤連結使用，使用者不存在時建立 UNBIND 狀態並紀錄各事業體的 Unbind 歷程，不變動既有 BLOCK 狀態
- 更新 MobileMgmClickTracingController 與多個 MobileTracingController，將 findByMidAndCreateUnbind 替換為 findByMidForTracking，確保追蹤邏輯的一致性與安全性
- 在 LOG_TARGET_ACTION_TYPE 列舉中新增 ACTION_AutoBinded 項目
- 移除無用的空檔案 doc/blank.txt

英文 Commit Message:
feat(user-trace): add auto-bind tracking and optimize user status query logic

- Add ACTION_AutoBinded record in UpdateBindingStatusService and ReceivingMsgHandlerMsgReceiveOp to distinguish between regular and auto-binding behavior
- Add global Unbind behavior logging in ReceivingMsgHandlerMsgReceiveOp
- Add findByMidForTracking method in LineUserService for tracking links, creates UNBIND status user when not found and logs Unbind history for each business unit without altering existing BLOCK status
- Replace findByMidAndCreateUnbind with findByMidForTracking in MobileMgmClickTracingController and multiple MobileTracingControllers for consistent and secure tracking logic
- Add ACTION_AutoBinded item to LOG_TARGET_ACTION_TYPE enum
- Remove unused empty file doc/blank.txt

建議 Branch Name:
feat/auto-bind-tracking

Git Label:
enhancement
```

---

## 多 Commit 拆分範例

當偵測到不相關的變更混在一起時，分別產出：

```
=== Commit 1/2 ===

繁體中文 Commit Message:
fix(push): 修正推播排程發送失敗時未正確更新狀態

- 修正 PushBySchedule 在 LINE API 回傳 429 時未將 msg_main 狀態回滾為 PENDING
- 新增重試計數器，超過 3 次標記為 FAILED 並寫入錯誤日誌

英文 Commit Message:
fix(push): fix push schedule not updating status correctly on send failure

- Fix PushBySchedule not rolling back msg_main status to PENDING when LINE API returns 429
- Add retry counter, mark as FAILED and log error after 3 attempts

建議 Branch Name:
fix/push-schedule-status-rollback

Git Label:
bug

=== Commit 2/2 ===

繁體中文 Commit Message:
refactor(richmenu): 抽取 Rich Menu 圖片上傳邏輯為獨立 Service

- 將 RichmenuController 中的圖片上傳邏輯抽取至 RichmenuImageService
- 統一圖片格式驗證與壓縮流程

英文 Commit Message:
refactor(richmenu): extract rich menu image upload logic into standalone service

- Extract image upload logic from RichmenuController to RichmenuImageService
- Unify image format validation and compression flow

建議 Branch Name:
refactor/richmenu-image-service

Git Label:
enhancement
```
