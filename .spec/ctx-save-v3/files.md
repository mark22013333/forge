---
slug: ctx-save-v3
stage: plan-build
generated: 2026-04-21
---

# ctx-save v3 程式碼產出清單

由 `/feature-workflow:plan-build` 啟動 4 人 Agent Team（closure-eng / analyze-eng / viewer-eng / distill-test-eng）於 2026-04-21 產出。

所有產出嚴守規格約束：Python stdlib only（零 pip 依賴）、原生 HTML/CSS/JS、繁體中文註解。

---

## 總覽

| 類別 | 新增 | 修改 | 小計 |
|------|------|------|------|
| Plugin-level hooks | 1 | 3 | 4 |
| Skill-level 核心 | 2 | 2 | 4 |
| analyze 模組 | 11 | 0 | 11 |
| Tests | 10 | 0 | 10 |
| Fixtures | 4 | 0 | 4 |
| Metadata | 0 | 2 | 2 |
| **合計** | **28** | **7** | **35** |

總程式碼行數：約 10,164 行（含 fixtures 與測試）。

---

## 新增檔案

### Plugin-level hooks（closure-eng）

| 檔案 | 行數 | 說明 |
|------|------|------|
| `plugins/ctx-save/scripts/ctx-reinject.py` | 446 | SessionStart `matcher=compact` hook — 讀未 restored snapshot、經 RestoreStrategy 注入 stderr、呼叫 `mark_restored` |

### Skill-level 核心（analyze-eng / distill-test-eng）

| 檔案 | 行數 | 說明 |
|------|------|------|
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-analyze.py` | 215 | Analyze CLI 入口 — 五階段 pipeline 協調器（scanner → classifier → rules → dedup → renderer） |
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-distill.py` | 511 | Heuristic summarizer CLI — 規則式去重/壓縮，輸出寫回 DB 並標 `distilled_from_id` |

### analyze 模組（analyze-eng）

| 檔案 | 行數 | 說明 |
|------|------|------|
| `analyze/__init__.py` | 36 | Package 入口，export 主要 DTO 與 pipeline 函式 |
| `analyze/scanner.py` | 225 | JSONL → `Event[]`（Immutable DTO）轉換器 |
| `analyze/classifier.py` | 134 | 五類 token 分類（conversation / files / tools_schema / tools_runtime / system）+ SessionBaseline |
| `analyze/dedup.py` | 115 | 四層防噪（Signal validation / Self-calibration / Root-cause dedup / Budget） |
| `analyze/renderer.py` | 151 | `Report` → Markdown 輸出 + focus hint 可貼指令 |
| `analyze/rules/__init__.py` | 29 | Rules package entry（export registry） |
| `analyze/rules/registry.py` | 63 | Rule Registry 模式 — 註冊中心 |
| `analyze/rules/duplicate_read.py` | 58 | Rule 1：同檔多次 Read 偵測 |
| `analyze/rules/autodump_cluster.py` | 61 | Rule 2：PreCompact autodump 聚集偵測 |
| `analyze/rules/unused_mcp_server.py` | 73 | Rule 3：未使用 MCP server schema 偵測 |
| `analyze/rules/claudemd_bloat.py` | 67 | Rule 4：CLAUDE.md 爆量偵測 |
| `analyze/rules/short_turns_noise.py` | 64 | Rule 5：短對話回合噪音偵測 |
| `analyze/rules/user_prompt_spike.py` | 51 | Rule 6：使用者提示暴衝偵測 |

所有 rule 皆為 pure function（≤ 60 行），透過 registry 註冊，dedup 層統一去重。

### 測試（distill-test-eng + analyze-eng）

| 檔案 | 行數 | 負責 | 說明 |
|------|------|------|------|
| `tests/__init__.py` | 1 | distill-test-eng | Package marker |
| `tests/_loader.py` | 48 | distill-test-eng | hyphen filename loader helper（`importlib.util.spec_from_file_location`） |
| `tests/run_all.py` | 68 | distill-test-eng | 測試主入口（stdlib unittest discovery） |
| `tests/test_migrations.py` | 185 | distill-test-eng | Migration idempotency — 跑兩次不重複、downgrade 後重跑 OK |
| `tests/test_reinject_contract.py` | 149 | distill-test-eng | Hook 契約 — DB 不存在 / lock / None snapshot 三情境皆 exit 0 |
| `tests/test_distill.py` | 118 | distill-test-eng | Heuristic summarizer 規則覆蓋 |
| `tests/test_classifier.py` | 185 | analyze-eng | 五類 token 分類驗證 |
| `tests/test_rules.py` | 321 | analyze-eng | 七情境覆蓋六條 rule（正例 + 反例 + edge） |
| `tests/test_dedup.py` | 149 | analyze-eng | 四層防噪驗證 |
| `tests/test_renderer.py` | 212 | analyze-eng | Markdown 輸出 + focus hint 驗證 |

**測試結果**：`python3 tests/run_all.py` → **80 個測試全通過**（Ran 80 tests in 0.261s）。

### Fixtures（distill-test-eng）

| 檔案 | 行數/KB | 說明 |
|------|---------|------|
| `tests/fixtures/session_light.jsonl` | 27 行 / 5.3 KB | 輕量乾淨 session（底線 baseline） |
| `tests/fixtures/session_heavy_duplicate.jsonl` | 21 行 / 19 KB | 含 duplicate_read + autodump_cluster |
| `tests/fixtures/session_mcp_unused.jsonl` | 13 行 / 4.7 KB | 未使用 MCP schema 情境 |
| `tests/fixtures/transcript_for_distill.txt` | 169 行 / 2.9 KB | distill 壓縮規則驗證 transcript |

---

## 修改檔案

### Plugin metadata

| 檔案 | 變更說明 |
|------|---------|
| `plugins/ctx-save/.claude-plugin/plugin.json` | version `2.3.1` → `3.0.0`，description 補「compact 後自動還原閉環」 |
| `plugins/ctx-save/.claude-plugin/hooks.json` | SessionStart 從單一 `matcher:*` 拆為 `startup`/`resume`/`compact` 三個 matcher；`compact` 綁 `ctx-reinject.py`（timeout 15s） |

### Plugin-level hooks

| 檔案 | 變更說明 |
|------|---------|
| `plugins/ctx-save/scripts/ctx-autodump.py` | +3329 bytes — 補 `context_percentage` 解析、snapshot source 標 `"auto-precompact"`、寫 compact_events 表（fallback 相容） |
| `plugins/ctx-save/scripts/ctx-config.py` | +755 bytes — 新增 `restore_strategy`（off/summary/full/ask）與 `max_reinject_bytes` 欄位 + CLI getter/setter |

### Skill-level 核心

| 檔案 | 變更說明 |
|------|---------|
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-db.py` | v2.3 → v3：新增 5 個 migration 函式（persistent / restored_at / distilled_from / diagnoses_table / compact_events_table）；flag 換代 `.migration-done` → `.migration-v3-done`；新增 13 個 Facade 方法（pin/unpin、mark_restored、list_persistent、list/insert/get/delete_diagnosis、insert/list_compact_events、get_dashboard_health 等）；save_distilled 介面 |
| `plugins/ctx-save/skills/ctx-save/scripts/ctx-viewer.py` | 2.1.0 → 3.0.0：新增 10 個 v3 API endpoints（GET diagnoses/persistent/compact-events/dashboard-health、POST diagnoses.run/distill/pin、DELETE diagnoses）；前端 hash router 改六 tab（dashboard/timeline/diagnostics/persistent/saves/stats）；SVG token pie chart + horizontal timeline；所有 CSS 擴充 |

---

## 里程碑對應

| 里程碑 | 對應任務 | 狀態 |
|--------|---------|------|
| **M1** 閉環核心 | #1, #2, #3, #4, #5 | ✅ |
| **M2** 診斷基礎（analyze + 4 層防噪） | #8 (distill)、analyze 模組 11 檔 | ✅ |
| **M3** Web Viewer 四 tab | #6, #7 | ✅ |
| **M4** Distill / Persistent / compact_events | #1（含這些 migration）、#8 | ✅ |
| **M5** 打磨、驗證、文件 | tests #10-#18（80 測試全通過） | ✅ 程式碼層面 |

---

## 後續可用指令

- `/plan-verify` — 運行時驗證（實際 import + smoke test 全流程）
- `/plan-review` — Agent Teams 3 人程式碼審查（邏輯 + 品質 + 安全）
- `/plan-close` — 結案並同步 Notion

---

## 驗證紀錄

- Python AST parse：全數 OK
- `python3 tests/run_all.py`：Ran 80 tests in 0.261s — **OK**
- Migration idempotency：✅（跑兩次不重複、downgrade 後重跑 OK）
- Hook 契約三情境：✅（DB 不存在 / lock / None snapshot 皆 exit 0）
- 五階段 pipeline integration：✅（三個 golden fixture 通過）
- 零外部依賴：✅（純 Python stdlib + 純 vanilla JS/SVG/CSS）
