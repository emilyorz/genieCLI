# 貢獻指南

感謝協助改善 GenieCLI。專案要求 **Python 3.10+**；請讓修改範圍小、可測試，並維持使用者文件與實際指令一致。

## 開發環境

```bash
git clone <repository-url>
cd genieCLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install trino              # 修改或手動驗證 direct Trino 路徑時需要
genie --help
```

LLM、Trino 與 MCP 的本機設定可用 `genie setup`、`genie setup trino`、`genie setup mcp` 建立。不要提交 `~/.genie/` 或 `~/.config/genie/` 中的憑證與個人設定。

## 測試與格式檢查

先跑受影響功能的 focused test，再跑完整測試：

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_cli_coverage.py -q
.venv/bin/python -m pytest -q
git diff --check
```

依變更選擇更精準的測試檔。例如修改 `/trino-research` 共用規則或 routing 時，至少執行：

```bash
.venv/bin/python -m pytest \
  tests/test_preflight_state_machine_acceptance.py \
  tests/test_dual_path_rule_id_equivalence.py \
  tests/test_plan_cost_core.py -q
```

若變更報告或步驟輸出，也應執行 `tests/test_step_trace.py` 與相關 evidence coverage 測試。測試名稱與範圍應隨實際修改調整；不要以無關的全套結果取代應跑的 focused test。

## MCP/direct parity

`/trino-research` 有 MCP 與 `--direct` 兩條 adapter 路徑。修改共同 preflight、靜態規則、診斷、候選准入、驗證或 report contract 時，請確認兩邊仍有相同的語意：read-only gate、失敗候選不取代 baseline，以及相同的 rule-id 契約。adapter 可因連線、metric 或 metadata 能力不同而有不同細節；不要藉由 silent fallback 隱藏這些差異。

## 如實記錄證據

unit test、mock、離線 AST 分析與 EXPLAIN 是有用證據，但不等於實際 Trino 的 row-value 等價或效能提升。只有真的在可辨識的 live Trino/LLM 環境執行 baseline、候選與必要驗證時，才可宣稱 live evidence。無法取得的證據應標為 unavailable、advisory 或 unverified，並說明限制；不要將推測寫成測量結果。

## 提交前檢查

- 新增或更新與修改相符的測試與文件。
- 確認命令、旗標、預設值與路徑可由程式碼驗證。
- 執行 focused pytest、完整 pytest 與 `git diff --check`。
- 不提交 token、密碼、個人連線設定或未經遮罩的 live 查詢資料。
