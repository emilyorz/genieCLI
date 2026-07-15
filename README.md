# GenieCLI

GenieCLI 是以 AI 輔助調校 **Trino SQL** 的命令列工具。它先整理 SQL 結構、EXPLAIN 與可用的執行證據，再請模型提出候選改寫；不是把模型回覆直接當成可執行答案。也支援 Oracle → Trino 的遷移輔助。

**三個安全重點**

- **先檢查再執行：**讀取型研究會先做 read-only 與 preflight 檢查；寫入或 DDL 的 `--file` 輸入只做離線分析，不執行 SQL。
- **候選必須驗證：**靜態、計畫結構、成本與可取得的結果等價證據會分層記錄；失敗或證據不足的候選不會取代目前基準 SQL。
- **路徑不偷換：**啟用 MCP 時使用 MCP；MCP 不可達會明確失敗，不會悄悄改用直連。

## 快速開始

### 1. 安裝

需要 **Python 3.10+**、一個 LLM 後端，以及可連線的 Trino（執行直連研究時）。

```bash
cd genieCLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install trino              # --direct 與 Trino 直連工具需要
```

### 2. 設定並檢查

```bash
genie setup                    # 設定 LLM：Ollama、OpenAI 相容、Anthropic 或 TGenie
genie setup trino              # 建立直連 Trino profile
genie setup mcp                # 選用：設定 Trino MCP server
genie doctor                   # 檢查 Python、依賴、LLM、Trino 與 MCP
```

LLM 設定寫入 `~/.genie/config.toml`；Trino profile 寫入 `~/.config/genie/trino.json`；`genie setup mcp` 寫入 `~/.config/genie/mcp.json`。`genie doctor` 會檢查所有整合，因此尚未設定或無法連線的 Trino/MCP 項目可能顯示 `FAIL`；請依實際要使用的路徑判讀結果。

### 3. 進行第一次研究

```bash
genie
```

在互動提示中選擇一條路徑執行：

```text
# MCP：需先完成 genie setup mcp
> /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3

# direct：需先完成 genie setup trino 並安裝 trino 套件
> /trino test
> /trino-research --direct --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
```

`--file` 可避免貼上多行 SQL 時的歧義。先只看診斷則使用：

```text
> /trino-research --file query.sql --diagnose-only
```

---

## `/trino-research` 使用指南

這是唯一的 Trino 調校入口：先取得基準或診斷，根據靜態規則與計畫訊號產生方向，再有限次地提出、測量與驗證候選 SQL。

### 常用旗標

```text
/trino-research [--file <path>] [--metric <metric>] [--iterations <n>] [--runs <n>]
                [--safe-limit <n>] [--query-timeout <seconds>]
                [--long-query | --no-long-query]
                [--long-query-threshold <seconds>] [--max-fallbacks <n>]
                [--diagnose-only] [--direct]
```

| 旗標 | 說明 |
| --- | --- |
| `--file <path>` | 讀取 SQL 檔；未指定時改為互動貼上。 |
| `--metric <metric>` | 調校目標。可用 `query_time_ms`、`cpu_time_ms`、`wall_time_ms`、`physical_input_bytes`、`processed_rows`、`total_splits`、`peak_memory_bytes`；實際可用項目依路徑而定。 |
| `--iterations <n>` | 最大改寫輪數；direct 預設 5，MCP 互動預設 1。 |
| `--runs <n>` | 每個候選的驗證次數，取中位數；預設 3。 |
| `--query-timeout <seconds>` | 單次查詢 timeout；預設 300 秒。 |
| `--safe-limit <n>` | 不改寫 logical SQL；執行量測時另以外層 `LIMIT` 產生受限 execution SQL。受限結果不可當作完整結果等價的證明。 |
| `--diagnose-only` | 產生靜態與 EXPLAIN 導向診斷，不執行原始 baseline 查詢或迭代。 |
| `--no-long-query` | baseline 超過門檻時只產生導向報告；門檻預設 60 秒。 |
| `--max-fallbacks <n>` | 最終 L3 結果等價驗證可嘗試的候選上限；預設 3。 |
| `--direct` | 強制跳過 MCP，改用本機 `trino` Python driver。 |

`--long-query` 是預設行為：慢 baseline 仍可進入調校。候選或驗證若超過 baseline wall time，會被視為失敗，不會成為新的 best。

### MCP 與 direct 如何選

- **MCP（預設，已啟用時）：**`genie setup mcp` 後，`/trino-research` 透過 Trino MCP server 執行，可使用 server 提供的查詢、EXPLAIN 與 metadata 工具。讀取型 SQL 若 MCP 未啟用或不可達，指令會報錯，避免誤以為測到 MCP 實際上卻用了別的連線。
- **direct：**使用 `--direct`，並安裝 `trino` 套件與設定 `genie setup trino` 的 profile。它透過 `trino.dbapi` 直接連 Trino；不需要 MCP server。
- **共同語意：**兩條路徑都使用 read-only preflight、靜態規則與驗證／報告概念，但連線、可用 metric 和 metadata 依 adapter 與環境而不同。

範例：

```text
> /trino-research --file slow.sql --diagnose-only
> /trino-research --direct --file slow.sql --metric wall_time_ms
> /trino-research --file slow.sql --no-long-query
```

### 寫入與 DDL

當 `--file` 內是 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`DROP`、`ALTER`、`TRUNCATE`、`RENAME`、授權、交易控制或 `CALL` 等有副作用 SQL，GenieCLI 會改走 write-analysis：只做靜態分析與可用的 LLM advisory。

它**不會**執行 SQL、跑 EXPLAIN、benchmark、套用 `--safe-limit`、連 MCP/Trino 或宣稱結果等價。報告中的建議需由使用者在已核准的環境自行審閱與執行。MCP 的互動貼上模式在取得 SQL 前仍可能先檢查 MCP 可達性；要在 MCP 離線時分析 write/DDL，請使用 `--file`。

### 報告輸出

報告預設寫在目前工作目錄；請從終端顯示的實際路徑取得檔案。

| 情境 | 檔名／位置 |
| --- | --- |
| direct 一般研究 | `./report/trino-research-YYYYMMDD-HHMMSS.md` |
| MCP 一般研究 | `./trino-research-mcp-YYYYMMDD-HHMMSS.md` |
| `--diagnose-only` 或停止慢查詢迭代 | `./report/trino-research-diagnose-YYYYMMDD-HHMMSS.md` |
| 無資料或找不到 schema/table/catalog | `./report/trino-research-nodata-YYYYMMDD-HHMMSS.md` |
| write/DDL 離線分析 | `./report/trino-research-write-analysis-YYYYMMDD-HHMMSS.md` |

報告會保留基準、候選、失敗原因與已取得的驗證證據。沒有 live 結果比對或執行量測時，結論是 advisory／unverified，不代表語意相同或一定加速。

---

## 安全保證，用白話說

1. **模型沒有放行權。**模型可以建議 SQL；程式仍會做 SQL 類型、lint、結構與可用驗證檢查。
2. **原始 SQL 是判斷基準。**read-only 檢查對原始 logical SQL 做判斷；`--safe-limit` 不會回寫成下一輪的語意來源。
3. **不以失敗換掉基準。**執行、timeout、驗證或必要證據失敗時，候選不會取代 baseline。
4. **不把離線測試說成線上證明。**靜態 AST、mock 或 EXPLAIN 訊號有其價值，但不等於 row-value 等價或實際效能提升。
5. **DDL 不會被一般讀取調校自動產生並執行。**CTAS 或 materialized view 只會是建議；需要 scratch schema、權限、清理策略與明確使用者選擇。

這些機制降低意外，但不能取代資料擁有者的審核、權限控管與正式環境變更流程。

## 其他互動指令

| 指令 | 用途 |
| --- | --- |
| `/trino` | 管理 Trino profile，`/trino test` 可測試連線。 |
| `/trino-research` | Trino SQL 診斷與有限迭代調校。 |
| `/new`、`/sessions`、`/load <n>` | 建立、列出與載入對話。 |
| `/skills` | 列出已發現的工具。 |
| `/model <name>`、`/reasoning` | 切換模型或 reasoning 等級。 |
| `/exit` | 離開。 |

## Oracle → Trino

內建工具可轉譯 Oracle SQL、查函數與型別對應、列出 Trino 限制，以及分析 stored procedure 遷移複雜度。請在互動對話描述要轉換或檢查的 Oracle SQL；轉譯結果仍應在目標 Trino 環境驗證，尤其是 NULL、日期、字串與程序語意。

## 架構速覽

```text
CLI / chat → Provider、SkillRegistry、OutputSink
                   ├─ mcp_trino：MCP server adapter
                   ├─ trino_query：trino.dbapi direct adapter
                   └─ oracle2trino：遷移工具
```

`/trino-research` 的共同控制面負責 preflight、診斷、候選准入與報告；MCP/direct adapter 負責各自的連線與執行細節。深入設計見 [架構文件](docs/doc-layer/ARCHITECTURE.md) 與 [程式碼地圖](docs/doc-layer/CODEMAP.md)。

## 限制

- 需要 Python 3.10+；`trino` driver 是 direct 路徑的額外安裝項目。
- LLM 建議品質取決於模型、提示與資料／計畫證據；複雜語意不可只靠文字審閱。
- MCP server 能提供的工具、Trino connector 支援與權限，會影響診斷深度。
- `WITH`/CTE 不是可假定的快取；materialization 有副作用，僅應在明確流程中採用。

## 文件索引

- [Trino 調校指引](docs/trino-tuning-guidance.md)：優化方向、materialization 邊界與官方參考。
- [Skill 撰寫指南](docs/skill-authoring.md)：最小 skill 結構與範例。
- [貢獻指南](CONTRIBUTING.md)：開發環境、測試與證據原則。
- [架構文件](docs/doc-layer/ARCHITECTURE.md)／[程式碼地圖](docs/doc-layer/CODEMAP.md)：深入實作導覽。

## 使用前提醒

- 先在非正式或有查詢配額的環境確認建議，再帶入正式工作負載。
- `--safe-limit` 適合控制量測範圍，不適合驗證完整報表或下游資料集。
- 結果等價檢查的可用程度取決於資料量、權限與 query timeout；請閱讀報告中的證據狀態。
- 若只需要 SQL 結構方向，優先使用 `--diagnose-only`，避免不必要的 baseline 執行。
- Oracle 遷移工具提供轉換與限制資訊，不會替代目標系統的整合測試。
- 需要更多 Trino 背景時，從文件索引的調校指引與官方連結開始。
- 使用 `genie doctor` 可快速確認目前 Python 版本與連線依賴是否符合預期。
- 不要將 LLM 回覆、離線診斷或 mock 結果記錄成已完成的 live 效能驗證。
