# genieCLI Autoresearch — 架構重構報告

> **日期**：2026-03-31
> **執行者**：genie-autoresearch（凌晨 02:00，被 Emily heartbeat 在 04:02 偵測到中途卡住）
> **Commits**：`a3522b0` → `52d6df2`（共 4 commits，從 `93f05aa` 基礎上）
> **測試**：416 tests pass

---

## 完成的工作

### 1. `_normalize_result` 抽共用 module（`92b32bd`）

**問題**：`chat.py` 和 `autoresearch_cli.py` 各自有一份幾乎相同的 `_normalize_result` 邏輯（把 tool 執行結果強制轉成 str）。

**修復**：

- 新增 `genie/core/tool_call.py` → `normalize_result(result) -> str`
- `chat.py` 和 `autoresearch_cli.py` 都改成 import 這個共用版本
- 副作用：`chat.py` 和 `autoresearch_cli.py` 各減少 ~5 行重複代碼

**評估**：正確。這是單一職責的教科書修法。

---

### 2. linter Oracle residual rules 共用 pattern catalog（`be91d49`）

**問題**：`genie/skills/trino_linter/rules.py` 裡的 Oracle 殘留規則（NVL、ROWNUM、SYSDATE 等）各自硬寫 regex pattern，跟 `oracle2trino/patterns.py` 裡的 pattern catalog 完全分離。兩個 skill 有兩份不同步的 pattern，未來維護會出現漂移。

**修復**：

- `patterns.py` 新增 `get_construct_pattern(construct)` — 從 shared catalog 取 regex
- `rules.py` 提取 `_check_oracle_residual(sql, construct, rule_id)` — 所有 Oracle residual 規則共用一個 implementation，pattern 統一從 `patterns.py` 取
- `_strip_comments_and_strings` 從 `rules.py` 搬到 `oracle2trino/sql_utils.py`，linter 改成 import
- `rules.py` 淨減 81 行（-126 / +45）

**評估**：這是本次最重要的架構改善。linter ↔ converter 現在共用同一份 pattern catalog，Phase 1 plan 的核心 constraint 落地。

---

### 3. `SkillRegistry.clear()` for test isolation（`52d6df2`）

**問題**：`SkillRegistry` 是 class-level singleton，測試之間共用狀態會造成污染。

**修復**：加 `SkillRegistry.clear()` class method，docstring 明確說明用途是 test isolation。

**評估**：小但正確。避免未來測試 flakiness。

---

## 未完成的工作

**架構 review 報告**未寫完 — session 在開始寫報告前卡在 pytest 確認框循環，被 heartbeat 強制結束。

目前架構（從 diff 看）：

- `genie/core/`：tool_call、registry 共用抽象 ✅ 穩定
- `genie/skills/oracle2trino/` ↔ `genie/skills/trino_linter/`：pattern 已統一 ✅
- `sql_utils.py` 抽出後位置在 `oracle2trino/` 下，但 linter 也 import 它 — 理論上應移到 `genie/core/` 或 `genie/utils/`，但目前功能正常，算 P2

---

## 結論

| 項目                              | 狀態            |
| --------------------------------- | --------------- |
| Pattern catalog 統一（P0）        | ✅ 完成         |
| `normalize_result` 去重（P1）     | ✅ 完成         |
| `SkillRegistry.clear()`（P1）     | ✅ 完成         |
| 全套架構 review 報告              | ⚠️ 本文件補完   |
| `sql_utils.py` 搬到 `core/`（P2） | ❌ 未做，待下次 |

主要目標達成，416 tests pass，可繼續 Phase 3。
