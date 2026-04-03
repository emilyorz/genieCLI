# 2026-03-28 Converter Hardening

## Meta

- date: 2026-03-28
- task level: M
- source request: GenieCLI Phase 2 Oracle → Trino 轉換器結構化輸出加強
- agent target: Claude Code
- schema: v1

## Plan

### Scope Summary

把 `oracle2trino` 裡 `transpile_sql` 與 `analyze_oracle_sp` 從純文字輸出改成穩定的結構化 JSON，讓上層 agent / CLI 能直接消費。同步抽出 linter ↔ converter 共用的 pattern catalog，避免 Oracle construct mapping 各寫各的。這次不重做整個 skill 架構，也不做真正的 SP 自動重寫引擎。

### Reuse Check

- 沿用 `genie/skills/oracle2trino/__init__.py` 既有 `_sqlglot_transpile()`、YAML DB 載入與 tool 註冊，不重開第二套 skill。
- 沿用 `genie/skills/trino_linter/analyzer.py` 的 dataclass → `to_dict()` 模式，converter 結果也應走同類型 model。
- 可抽出共用 `pattern catalog` / result model 到 `genie/skills/oracle2trino/` 下新模組，或更中性的 shared module；先以最小 diff 為準。
- `lookup_oracle_function`、`lookup_oracle_type`、`list_trino_limitations` 維持原樣，不順手改 API。
- 無需碰 provider / output sink / session / CLI routing。

### Minimal Diff Expectation

- 預期主要修改 `genie/skills/oracle2trino/__init__.py`，再新增 1–2 個共用 model / catalog 檔案。
- 允許新增 dataclass，例如 `UnsupportedConstruct`、`ConversionResult`，但不要引入新 framework。
- `trino_linter` 最多只改 import 路徑或 rule metadata 接線，不重寫 analyzer/rules 架構。
- 不該碰 CLI command surface、其他 skills、外部資料 schema。

### File Impact

- `genie/skills/oracle2trino/__init__.py`
- `genie/skills/oracle2trino/*`（新增 result model / pattern catalog）
- `genie/skills/trino_linter/*`（若需接共享 catalog）
- `tests/test_oracle2trino*.py`
- `tests/test_trino_linter*.py`（補 catalog 共用 regression）

### Edge Cases

- sqlglot 轉不動時，仍要回傳合法 JSON，不能退回散亂字串。
- 同一段 SQL 同時含多個 Oracle construct 時，`unsupported` 不能漏報或重複灌水。
- `analyze_oracle_sp` 遇到 PL/SQL block、cursor、exception handling 時，要明確標成人工處理，不要假裝已轉完。
- `confidence` 不能拍腦袋；至少要跟 unsupported 數量與 severity 有一致規則。
- 共用 catalog 若只被 converter 用、linter 沒接上，這次就算沒做完。

### Test Matrix

- 用可安全機械轉換的 Oracle SQL 驗證 `transpile_sql` 回傳 JSON，且包含 `converted_sql/unsupported/warnings/confidence/manual_fix_notes` 五個核心欄位。方法：pytest assert dict keys + 值型別。
- 用 `ROWNUM`、`CONNECT BY`、`LISTAGG`、`EXECUTE IMMEDIATE` 案例驗證 `unsupported` 內容完整，含 `construct/severity/message/suggestion`。方法：pytest 逐項 assert。
- 用 PL/SQL stored procedure 範例驗證 `analyze_oracle_sp` 不再輸出大片說明文，而是結構化結果 + 合理的人工修補 notes。方法：pytest assert JSON shape 與 notes 內容。
- 驗證 `confidence` 會隨 high-severity unsupported 增加而下降。方法：unit test 比較 clean SQL vs unsupported-heavy SQL 的 score。
- 驗證 linter 與 converter 共用同一份 pattern metadata，而不是各自 hardcode 一份。方法：unit test 檢查 shared catalog import 與關鍵 construct 對齊。

### Out of Scope

- 不做 Oracle stored procedure → Python orchestration 的自動產碼。
- 不做 function/type YAML database 的大規模內容擴編。
- 不做新 CLI 指令或新的獨立 skill。
- 不做 Trino 線上驗證、執行期測試、connector capability 探測。
- 不順手重構整個 `oracle2trino` package 成多層 architecture，這題先求輸出結構化與知識共用。

## Review

### 第一輪：Codex（acpx）

- 總評：**D**，NEEDS WORK
- 3 個 findings（1 High / 2 Medium）

| ID  | Severity | 問題                                                               | 修復狀態               |
| --- | -------- | ------------------------------------------------------------------ | ---------------------- |
| H1  | High     | converter \_detect_unsupported() 沒 strip comments/strings，會誤報 | ✅ 已修                |
| M1  | Medium   | catalog 只共用 message/suggestion，沒共用 regex pattern            | 📝 Tech debt，不 block |
| M2  | Medium   | 測試缺 comment/string false positive 覆蓋                          | ✅ 已補 4 個           |

### 第二輪：Emily Reality Check

- H1 確認為真 bug（同 Phase 1 H1 相同類型）
- M1 記為 tech debt，不影響正確性
- 修復 commit：`71224b8`
- 修復後 97 tests 全過

## QA

| #   | Test Item                                 | Status  | Evidence                        |
| --- | ----------------------------------------- | ------- | ------------------------------- |
| TM1 | transpile_sql 回傳 JSON + 5 核心欄位      | ✅ PASS | all keys present                |
| TM2 | ROWNUM/CONNECT BY/LISTAGG → unsupported   | ✅ PASS | {LISTAGG, CONNECT BY, ROWNUM}   |
| TM3 | PL/SQL block → analyze_oracle_sp 結構化   | ✅ PASS | unsupported > 0                 |
| TM4 | confidence 隨 unsupported 下降            | ✅ PASS | clean=1.0, dirty=0.0            |
| TM5 | 共用 catalog 被 linter + converter import | ✅ PASS | get_construct_meta('NVL') works |
| R1  | comment 裡的 NVL 不誤報                   | ✅ PASS | NVL not in unsupported          |

## Closeout

### Delivery Summary

**Phase 2: Oracle → Trino 轉換器結構化輸出** — 完成 ✅

| Item              | Detail                                                                 |
| ----------------- | ---------------------------------------------------------------------- |
| Commits           | `dde43cd` feat + `71224b8` fix                                         |
| New files         | models.py, patterns.py, test_oracle2trino_structured.py                |
| Tests             | 97 passed (56 linter + 41 converter)                                   |
| Structured output | converted_sql + unsupported + warnings + confidence + manual_fix_notes |
| Shared catalog    | 19 Oracle constructs in patterns.py                                    |

### Tech Debt

- M1: linter regex 和 catalog regex 尚未完全統一（各自 hardcode），未來應收斂

### PR Ready？

**Yes** — Phase 1 + Phase 2 都在 main 上，可用。
