# 2026-04-03 技術債清理 + Trino Linter 強化

## Meta

- date: 2026-04-03
- task level: L
- source request: Sam — 用完整開發流程做 genieCLI
- agent target: Claude Code（互動模式，plan mode）
- schema: v1

## 任務分級依據

- Blast radius：高。main.py 是唯一入口之一，改壞等於 CLI 全壞
- Feedback velocity：中。有 420 tests 但 coverage 未確認，舊入口無 test
- 跨 7+ 檔案 + 需派 coding agent → **L 級**

## Plan

### Requirement Challenge

1. **新舊架構並存是真正的問題嗎？** — 是。`main.py`（949 行，argparse）和 `genie/cli.py`（313 行，Typer）做同一件事。`pyproject.toml` 定義的入口是 `genie.cli:main`，但 `main.py` 仍是原始入口。tests 全走新架構（`from genie.*`），舊檔案零 test coverage。實際上 main.py 已經是 dead code，只是沒人敢刪。
2. **「thin wrapper」vs「直接刪」？** — 直接刪。main.py 不被任何 test 或 pyproject.toml 引用。保留它只會讓人困惑。但要確認沒有外部 script 或文件引用它。
3. **Linter 已經存在（`trino_linter/`），還需要「做 Phase 4」嗎？** — Phase 4 的 Linter 已經在 Phase 1 plan 裡做完了（`trino_linter/rules.py` 11 條 rules，`analyzer.py` 完整）。需要的是：(a) 確認 Linter 品質，(b) 修 PR #6/#9 的 critical bugs，(c) 清理技術債。不是重寫 Linter。

### Scope Summary

分三個 stage 依序執行：

1. **Stage A — 技術債清理**：刪除 dead code（`main.py`、`api.py`、`skill_runner.py`、`page_context.py`、`session.py`、`config.py`、`skills/` 舊目錄），確認 tests 全過。
2. **Stage B — Bug Fix**：修 PR #6/#9 提到的 critical issues（Typer routing options 重複、screenshot tool chain 斷裂）。
3. **Stage C — Linter 品質強化**：對 `trino_linter/` 做 adversarial review，確認 Phase 1 review 的 H1-H3/M1-M3 修復是否真的修好，補漏。

### Reuse Check

- `genie/` package 是唯一正式架構，全部沿用
- `tests/` 420 tests 全走 `genie.*`，不需改
- `trino_linter/rules.py`（319 行）+ `analyzer.py`（114 行）已完成，不重寫
- `oracle2trino/patterns.py`（237 行）是共用 catalog，linter 已在用

### Minimal Diff Expectation

- Stage A：刪 6 個舊檔 + `skills/` 舊目錄（~2400 行），不新增
- Stage B：改 `genie/cli.py` 和 `genie/skills/browser/tools.py`，最多 2-3 檔
- Stage C：改 `trino_linter/rules.py` + 補 test，最多 2 檔
- 不碰 `genie/core/`、`genie/providers/`、`genie/runtime/`

### File Impact

**Stage A — 刪除（確認無引用後）：**

- `main.py`（949 行）— 舊入口，pyproject.toml 不引用
- `api.py`（402 行）— 被 main.py 引用，genie/ 不引用
- `skill_runner.py`（261 行）— 同上
- `page_context.py`（382 行）— 同上（genie/ 有自己的 `genie/skills/browser/page_context.py`）
- `session.py`（72 行）— 同上（genie/ 有 `genie/session/`）
- `config.py`（43 行）— 同上（genie/ 有 `genie/core/config.py`）
- `skills/` 舊目錄（1352 行）— genie/skills/ 是正式版
- `runtime/` 根層級目錄 — `genie/runtime/` 的舊 copy（只差 import path）
- `test_tool.py`、`test_vision.py`（舊 test）
- `tgenie.sh`、`tgenie.bat` — 更新 `main.py` 引用為 `python -m genie`

**保留：**

- `grab_auth.py` — 被 `genie/providers/tgenie.py` subprocess 呼叫，不能刪

**Stage B — 修改：**

- `genie/cli.py` — Typer callback/options 修正
- `genie/skills/browser/tools.py` — screenshot tool chain

**Stage C — 修改/新增：**

- `genie/skills/trino_linter/rules.py` — 如有漏洞
- `tests/test_trino_linter*.py` — 補 regression

### Edge Cases

1. `main.py` 被 `tgenie.sh`/`tgenie.bat` 引用 → 要同步更新啟動腳本指向 `python -m genie`
2. `grab_auth.py` 不在新架構內但可能被外部使用 → 確認後決定保留或整合
3. 刪 `skills/` 舊目錄後，`__pycache__` 殘留不影響但要清
4. `runtime/` 目錄在根層級，可能引用舊模組 → 檢查 import chain
5. Typer options 重複的 bug 可能影響 `--skills` / `--debug` 等 global flag

### Test Matrix

1. **刪除舊檔後 420 tests 全過** — `python -m pytest -q`，PASS = 0 fail
2. **CLI 入口正常** — `python -m genie --help` 輸出包含所有子指令，exit 0
3. **`python -m genie tools --json` 列出所有 skills** — JSON 輸出含 `trino_linter`、`oracle2trino`、`browser` 等
4. **`tgenie.sh` 更新後可執行** — `bash tgenie.sh --help` exit 0（或確認改用 `python -m genie`）
5. **Screenshot tool chain 修復** — 補 unit test：mock CDP → 觸發 screenshot → 驗證 tool_call 第二次仍被 execute
6. **Trino linter adversarial：含 Oracle 殘留的 SQL 在 comment/string 中不被誤抓** — 對應 H1 修復，pytest assert findings = 0

### Out of Scope

- 不做 Phase 4 Converter hardening（Stage C 只驗 Linter）
- 不做 runtime/autoresearch 重構
- 不做 provider 層改動
- 不做 Web UI / HTTP API
- 不做 `grab_auth.py` 重寫（只確認依賴關係）
