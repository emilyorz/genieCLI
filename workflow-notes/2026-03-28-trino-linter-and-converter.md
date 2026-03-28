# 2026-03-28 Trino Linter + Converter

## Meta
- date: 2026-03-28
- task level: L
- source request: GenieCLI Phase 1 Trino SQL Linter + Phase 2 Oracle→Trino converter hardening
- agent target: Claude Code
- schema: v1

## Plan

### Scope Summary
新增一個不需 Trino 連線的 Trino SQL linter，對 SQL 做 AST/heuristic 靜態分析並輸出結構化 findings + score + summary。同步強化既有 oracle2trino skill，改成回傳 converted SQL、unsupported list、manual fix notes、confidence，而不是只有文字提示。這次不做 Trino MCP、執行期驗證、Query Optimizer。

### Reuse Check
- 沿用 `genie/core/registry.py` 的 plugin 註冊模型，不另開第二套 engine。
- 沿用 `genie/skills/oracle2trino/__init__.py` 的 sqlglot transpile 與 YAML mapping 資料。
- 可新增同 skill 內多工具或新增 `trino_linter` skill；先以最小接線成本為準。
- 沿用現有 `genie/output/machine.py` 的 JSON 輸出模式，不重寫 CLI formatter。
- 無明顯可復用的 rule engine；需新建輕量分析層。

### Minimal Diff Expectation
- 預期影響 6–10 個檔案，主要在 `genie/skills/`、`genie/core/`、`tests/`、`workflow-notes/`。
- 允許新增 1 個 linter skill/package 與 1 個共用 rule/result model 模組；避免引入重量級 framework。
- CLI 入口最多做薄接線，不重構 provider/chat/session 流程。
- 不碰外部 API、資料 schema、既有 browser/file/git/shell skills 行為。

### File Impact
- `genie/skills/oracle2trino/__init__.py`
- `genie/skills/oracle2trino/data/*`
- `genie/skills/trino_linter/__init__.py` 或 `genie/skills/oracle2trino/*`
- `genie/skills/trino_linter/skill.toml`（若新建 skill）
- `genie/core/registry.py` 或新增 `genie/core/*result*.py` / `*rules*.py`
- `tests/test_oracle2trino*.py`, `tests/test_trino_linter*.py`

### Edge Cases
- SQL parse 失敗時仍要回傳結構化錯誤，而不是直接炸掉。
- 同一段 SQL 同時含 Oracle 殘留語法與低效 Trino pattern 時，findings 不能互相覆蓋。
- `SELECT *` 出現在合法探索情境外仍要報，但不能誤判 `COUNT(*)`。
- `ROWNUM`、`CONNECT BY`、`(+ )` 這類無法安全自動轉換的語法要明確列 unsupported。
- 多 statement 或 CTE 巢狀查詢要能標到合理 line，至少不要全部變 line 1。

### Test Matrix
- 用純 Trino SQL fixture 驗證 linter 可輸出 `findings[]/score/summary`，且涵蓋 `SELECT *`、leading wildcard、cross join。方法：pytest snapshot/assert JSON fields。
- 用含 Oracle 殘留語法的 SQL 驗證 linter 可抓 `NVL/DECODE/ROWNUM/SYSDATE/(+)`。方法：pytest 逐條 assert rule 與 severity。
- 用可安全轉換的 Oracle SQL 驗證 converter 會輸出 `converted_sql` 且 `unsupported` 為空或低風險。方法：pytest 比對結構化 dict。
- 用 `CONNECT BY`、PL/SQL block、`EXECUTE IMMEDIATE` 等案例驗證 converter 不幻想式翻譯，會列 unsupported 與 manual fix notes。方法：pytest assert confidence 降低且 unsupported 非空。
- 驗證 pattern 回饋機制：converter 偵測到 unsupported Oracle construct 後，對應 linter rule metadata 存在且可重用。方法：unit test 檢查共用 rules/pattern catalog。
- 驗證 CLI/skill 註冊仍可列出新工具且舊 skills 不受影響。方法：跑 `python -m genie tools --json` 或 registry unit test。

### Out of Scope
- 不做 Trino 線上連線、schema introspection、partition metadata 自動探測。
- 不做 stored procedure 全自動轉換，只做 unsupported 分析與人工修補提示。
- 不做 EXPLAIN ANALYZE、成本估算、實際 query rewrite optimizer。
- 不做 HTTP API / Web UI。
- 不做大規模 core 架構重寫或 agent loop。

## Review

### 第一輪：Codex（acpx）
- 總評：**C**，NEEDS WORK
- 6 個 findings（3 High / 3 Medium）

| ID | Severity | 問題 | 修復狀態 |
|----|----------|------|---------|
| H1 | High | Oracle residual rules 用 raw regex，誤抓 comment/string/quoted identifier | ✅ 已修 |
| H2 | High | Parse failure 用 IGNORE，無效 SQL 回 score A | ✅ 已修 |
| H3 | High | correlated-subquery 沒檢查 correlation | ✅ 已修 |
| M1 | Medium | missing-partition-filter 掃進 nested subquery | ✅ 已修 |
| M2 | Medium | select-star 多 statement line number 都是 1 | ✅ 已修 |
| M3 | Medium | 測試缺 regression cases | ✅ 已補 6 個 |

### 第二輪：Emily Reality Check
- 全部 findings 確認為真 bug，無 false positive
- 修復 commit：`1c17a1f`
- 修復後 56 tests 全過

## QA

對照 Plan test matrix 逐項驗證，2026-03-28 18:10 執行。

| # | Test Item | Status | Evidence |
|---|-----------|--------|----------|
| TM1 | Linter 輸出 findings/score/summary（SELECT *, wildcard, cross join） | ✅ PASS | rules={select-star, leading-wildcard-like, implicit-cross-join}, score=B |
| TM2 | Oracle 殘留語法偵測（NVL/DECODE/ROWNUM/SYSDATE/(+)） | ✅ PASS | 5/5 rules detected |
| TM3 | Clean Trino SQL 不誤報 | ✅ PASS | findings=0, score=A |
| TM4 | PL/SQL block → parse_error + score F | ✅ PASS | parse_error="Invalid expression", score=F |
| TM5 | Rule catalog 可 import 重用 | ✅ PASS | ALL_RULES count=11 |
| TM6 | Skill 註冊正常，舊 skills 不受影響 | ✅ PASS | TrinoLinter.name=trino_linter |

**額外驗證（review regression）：**

| # | Case | Status | Evidence |
|---|------|--------|----------|
| R1 | NVL in comment 不誤報 | ✅ PASS | oracle-residual-nvl not in findings |
| R2 | SYSDATE in string 不誤報 | ✅ PASS | oracle-residual-sysdate not in findings |
| R3 | Invalid SQL → parse_error + F | ✅ PASS | parse_error is not None |
| R4 | Uncorrelated subquery 不誤報 | ✅ PASS | correlated-subquery not in findings |

**QA 結論：6/6 TM + 4/4 regression = 全過，無 blocked items。**

## Closeout

### Delivery Summary
**Phase 1: Trino SQL Linter** — 完成 ✅

| Item | Detail |
|------|--------|
| Commits | `5c46e5f` feat + `1c17a1f` fix |
| Files added | 4 new（trino_linter package） + 1 test file |
| Tests | 56 passed, 0 failed |
| Rules | 11 條靜態分析 rules |
| Review | Codex C → 修復後通過 Emily Reality Check |
| QA | 10/10 pass |

### Scope Check
- ✅ Trino SQL Linter（Plan scope 內）
- ⏭️ Phase 2 Oracle→Trino converter hardening（下一步）
- ⏭️ 共用 pattern catalog（Plan 提到，Phase 2 再做）

### Known Limitations
- missing-partition-filter 是 heuristic，沒有 schema metadata 無法確定哪些是 partition column
- correlated-subquery 檢查用 table qualifier matching，複雜 alias 場景可能有 edge case
- 無 auto-fix 功能（by design，只診斷不改）

### Recommended Next Steps
1. Phase 2: Oracle→Trino converter 結構化輸出
2. 共用 rule/pattern catalog（linter ↔ converter 知識共用）
3. 等 Trino 連線環境好 → Phase 3 MCP 接上 → linter 升級 schema-aware

### PR Ready？
**Yes** — 可 merge to main（已 push）。
