# GenieCLI

AI-powered Trino query tuning CLI. 用 LLM 自動優化 Trino SQL，結合靜態分析、EXPLAIN 診斷、自動迭代、result equivalence guard。

支援三種 AI 後端：TGenie gateway（公司內部）、**OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio）、**Anthropic**。

**核心功能：** Trino query 自動優化（autoresearch）、pre-execution directed diagnosis、長查詢迭代跳過診斷報告、Oracle → Trino SQL 遷移、Trino SQL 靜態分析、MCP Trino 整合。

**v5.0.0** — 聚焦 Trino query tuning，移除無關功能（browser automation、deepwiki），共用 pattern catalog 移至 core。

---

## 架構

GenieCLI 由 CLI/chat、共用 core、LLM provider、可發現 skills 與兩條 Trino
執行 adapter 組成。`/trino-research` 的 **MCP** 路徑透過 Trino MCP server 執行與取得
metadata；`--direct` 路徑以本機 `trino.dbapi` 連線。兩者共用 read-only/preflight
決策、靜態規則、plan-cost loop、診斷與報告契約；adapter 只負責各自的連線、量測與
資料列形狀轉換。

```text
┌──────────────── CLI / chat ────────────────┐
│ cli.py · chat.py · input.py · session       │
└───────────────┬─────────────────────────────┘
                │ Provider / OutputSink / SkillContext
┌───────────────▼─────────────────────────────┐
│ core                                         │
│ registry · config · context · lint · SQL     │
│ extraction · shared LLM advisory adapters    │
└───────┬───────────────────────────────┬──────┘
        │                               │
 ┌──────▼─────────┐              ┌──────▼──────────┐
 │ mcp_trino      │              │ trino_query     │
 │ MCP client     │              │ trino.dbapi     │
 │ MCP adapter    │              │ direct adapter  │
 └──────┬─────────┘              └──────┬──────────┘
        └────────── shared research contracts ────┘
                     │
       preflight → baseline → decompose → optimize
                     → recompose → verify
                     │
  StepTrace / HumanSink / MachineSink / Markdown reports
```

### `/trino-research` routing and safety model

在執行候選 SQL 前，兩條路徑都先檢查 logical SQL 的 read-only whitelist，並把
`--safe-limit` 視為已驗證的 execution policy；它不改寫 logical SQL。每次呼叫的
事實由純函式 `build_preflight_decision()` 產生不可變的 `PreflightDecision`，
`PreflightRoute` 是唯一的六態路由表：

| Route | 行為 |
| --- | --- |
| `DIAGNOSE_ONLY` | 不跑 baseline；產生 static / EXPLAIN / metadata（可用時）診斷。 |
| `NO_DATA` | baseline 空結果或已辨識的 table/schema/catalog not-found；轉 no-data 報告。 |
| `REAL_FAILURE` | 非 no-data baseline 例外直接呈現，不會誤包裝成 no-data。 |
| `LONG_QUERY_ABORT` | 使用者關閉 long-query tuning 且成本 gate 拒絕；輸出 directed report。 |
| `PLAN_COST_LOOP` | long-query opt-in 且 EXPLAIN 有 estimates；先以 plan cost 排名，再做 live 驗證。 |
| `STANDARD_LOOP` | 一般量測／迭代流程。 |

`PLAN_COST_LOOP` 的候選先經 lint、read-only gate、EXPLAIN 與 L1 plan-structure
檢查，僅將較低 cost 的候選送入有限次 L3 row-equivalence fallback 驗證。一般 loop
以 baseline 與候選的實際 metric 比較；兩者都保留失敗、timeout 與驗證 provenance，
不能以失敗候選取代 baseline。

### 目前解 Trino query 的核心設計

GenieCLI 不把整段 SQL 丟給模型後直接執行。核心做法是由 deterministic control
plane 控制安全、路由、成本排序與驗證；LLM 只在邊界內提出候選 rewrite：

```text
logical SQL
  │
  ├─ read-only gate + immutable ExecutionPolicy
  ├─ PreflightDecision（唯一六態路由）
  │
  ├─ baseline / EXPLAIN / static findings
  ├─ recursive CostNode tree → critical path → diagnosis brief
  │
  ├─ decompose → P1–P9 strategy selection → bounded fragment rewrite
  ├─ recompose full AST → cross-fragment rescan
  │
  └─ L1 structural + L2 EXPLAIN + L3 live equivalence
       └─ SHIP | NO_SHIP | UNVERIFIED | NO_COST_IMPROVEMENT
```

這個設計有四個硬邊界：

1. **logical SQL 與 execution SQL 分離**：read-only gate 永遠檢查 logical SQL；
   `--safe-limit` 存在不可變的 `ExecutionPolicy`，每次量測前才衍生 execution SQL，
   不會把包裝後 SQL 回灌成下一輪語義來源。
2. **路由由 state machine 決定**：baseline、no-data、real failure、long-query gate 與
   plan-cost loop 都收斂到 `PreflightDecision`。控制流程不靠分散的 boolean 或例外文字
   猜測，真實錯誤也不會被誤報成 no-data。
3. **LLM 沒有放行權**：模型只能從命名的 P1–P9 strategy menu 提出候選。
   deterministic gate 負責 parse、lint、read-only、結構、欄位形狀、成本與結果等價；
   任一必要證據缺失時，結果只能降級成 advisory 或 `UNVERIFIED`。
4. **MCP 與 direct 共用語義**：兩個 adapter 只處理連線、執行、量測與資料列形狀；
   preflight、diagnosis、rewrite admission、verification 和 report contract 共用同一套邏輯。

讀取型研究由五個公開階段組成。各階段遇到 cluster、parser 或 LLM 問題時，回傳 typed
unavailable / unverified 結果，不把未處理例外當成研究結論：

1. **baseline**：取得 EXPLAIN cost、plan signature，並在可執行時建立 row anchor。
2. **decompose**：用 sqlglot 拆出 CTE、root、derived table、JOIN RHS，以及
   `EXISTS`/`IN` predicate subquery；再依 static findings 與結構成本排序 monster
   fragments。
3. **optimize**：只從 P1–P9 strategy menu 選擇做法。SAFE 可提出 rewrite，TRAP
   必須通過等價驗證，DANGEROUS 只提供 advisory。預設是 evidence-only；片段 rewrite
   必須明確設定 `GENIE_FRAGMENT_REWRITE=1`，並受 `GENIE_FRAGMENT_REWRITE_CAP` 限制。
4. **recompose**：把核准的 fragment 放回完整 AST，重新掃描跨片段問題；遇到 block、
   parse uncertainty 或無法安全定位的 replacement 時，保留原 fragment。
5. **verify**：執行 baseline/candidate row equivalence、schema/column guards 與 cost
   comparison，最終 verdict 只有 `SHIP`、`NO_SHIP`、`UNVERIFIED` 或
   `NO_COST_IMPROVEMENT`。

`critical_path.py` 會遞迴建立 `CostNode` 樹，以 ordinal row magnitude、join blow-up、
correlated subquery、aggregate/distinct/limit reducer 和 static-rule penalty 估算結構
成本，再找出最重的 root-to-leaf path。CTE owner、derived table、JOIN RHS，以及任意深度
的 `UNION` / `UNION ALL` / `INTERSECT` / `EXCEPT` 都走同一個 query traversal；連鎖
set operation 不會再被當成「不是 SELECT」的 parse error。這仍是離線排序訊號，不是
live Trino cost；遇到 cartesian 或 correlated 結構時，報告會明確標示 offline
truth ceiling。bottleneck 會進入 deterministic diagnosis brief，讓 optimizer 先處理
證據最強的節點，而不是對整段 SQL 盲改。

P9 另外有 pure-AST fan-out verifier。只有原 correlated `EXISTS`、同一 inner relation
的 pre-aggregation CTE、GROUP BY grain 與回接 LEFT JOIN 完整綁定時，才會回報
`PROVEN_NO_FANOUT`，而且只代表 row-count safety。值等價、NULL 語意與實際加速仍需
L3 live evidence。Evidence coverage 會分開列出 L1（結構）、L2（EXPLAIN）與 L3
（live）的 `PASS` / `FAIL` / `PARTIAL` / `PENDING`，並標示 `SHIP`、`ADVISED` 或
`PENDING_LIVE`；報告層不會覆寫原本的 accept/reject 決策。

### 可觀測性與 LLM 邊界

`StepTrace` 是研究流程的有序 `StepEvent` 記錄，涵蓋 preflight route、baseline、
decompose、fragment、critical path、recompose、iteration 和 verify。HumanSink 顯示
精簡 breadcrumb，Markdown report 保留完整步驟與降級理由，MachineSink 輸出 NDJSON。

LLM provider（OpenAI-compatible/Ollama、Anthropic、TGenie）實作共用 `Provider`
protocol。`genie/core/llm_adapters.py` 把 `provider.complete_text()` 轉成供 advisory
與 fragment pipeline 使用的 `prompt -> text` callable；模型錯誤由階段本身降級處理。
`genie/core/sql_extraction.py` 集中處理 fenced SQL 擷取、CTAS inner query
extract/rewrap、column-shape guard 與 default-deny structural-equivalence 比對，避免 MCP
與 direct 各自解析模型輸出。

### 模組總覽

| 區域 | 主要內容 |
| --- | --- |
| `genie/` | Typer CLI、chat tool loop、互動輸入與 setup wizard。 |
| `genie/core/` | `Provider`、`OutputSink`、`SkillContext`、registry/config/context、lint、`llm_adapters.py`、`sql_extraction.py`。 |
| `genie/providers/` | OpenAI-compatible（含 Ollama）、Anthropic、TGenie adapters 與共用 HTTP/SSE 支援。 |
| `genie/output/` | Rich `HumanSink`、NDJSON `MachineSink`、`step_trace.py`。 |
| `genie/session/` | 對話 message/session JSON 持久化。 |
| `genie/skills/mcp_trino/` | MCP client、preflight state machine、診斷/rule gate、cost/critical path、P-strategies、strategy verification、五階段 pipeline。 |
| `genie/skills/trino_query/` | direct connection、量測、static rules R1–R10、plan signature 與 direct research adapter。 |
| `genie/skills/oracle2trino/` | Oracle → Trino transpile、函數 lookup、限制與 stored-procedure analysis。 |
| `genie/runtime/` | 通用 autoresearch 的 git checkpoint、metric comparison 與 TSV journal。 |
| `tests/` | 純函式、雙路徑 parity、state-machine、pipeline、critical-path、strategy/evidence 與整合測試。 |

---

## 快速開始

**你要的流程**：裝 → 設定 LLM + Trino → 驗證 → 跑優化 → 拿報告。以下 5 步走完就能用。

### 前置需求

- Python 3.9+
- Trino cluster（或 Docker container，本機 `localhost:8085` 也可）
- 一個 LLM backend（Ollama / OpenAI / Anthropic / TGenie，擇一）

### Step 1 — 安裝

```bash
cd genieCLI
python3 -m venv .venv && source .venv/bin/activate   # 建議 venv，避免污染系統 Python
pip install -e .
pip install trino                                    # Trino driver（跑優化需要）
```

**驗證：** `which genie` 應該指到 venv 裡的 `bin/genie`；`genie --help` 不報錯。

> `genie: command not found` 多半是 venv 沒 activate，或裝到 user site 但 PATH 沒含 `~/.local/bin`。進 venv 再跑最穩。

### Step 2 — 設定 LLM + Trino

互動 wizard（推薦）：

```bash
genie setup          # 選 LLM backend（Ollama / OpenAI / Anthropic / TGenie）
genie setup trino    # 設 Trino 連線 profile
genie setup mcp      # （選配）MCP Trino server，enable 後 /trino-research 會自動走 MCP
```

或手動編輯 `~/.genie/config.toml`（wizard 寫的也是這個檔）：

<details>
<summary>Ollama（本機 LLM，免費）</summary>

```toml
interface = "openai"
openaiApiKey = "ollama"
openaiBaseUrl = "http://localhost:11434/v1"
defaultModel = "qwen3.5:4b"
```

Ollama 會自動切 native `/api/chat` endpoint（非 `/v1`），以支援 `think=false`。

</details>

<details>
<summary>OpenAI</summary>

```toml
interface = "openai"
openaiApiKey = "sk-..."
openaiBaseUrl = "https://api.openai.com/v1"
defaultModel = "gpt-4o"
```

</details>

<details>
<summary>TGenie（公司內部）</summary>

```toml
interface = "tgenie"
endpoint = "https://your-ai-gateway.internal.company.com"
authToken = "your-token"
defaultModel = "gemini-2.5-flash"
```

</details>

### Step 3 — 驗證環境

```bash
genie doctor
```

會逐項檢查：Python ≥ 3.9、`genie` 在 PATH、`trino` / `sqlglot` 依賴、LLM provider、Trino 連線、MCP 可達性。看到全 `OK`/`SKIP` 就能往下；`FAIL` 依訊息修（卡住也可直接跑，`/trino-research` 會回報更具體的錯）。

### Step 4 — 跑第一個優化

```bash
genie --skills
```

進 chat loop 後：

```
> /trino test                 # 確認 Trino profile 能連
> /trino-research             # 互動模式：貼 SQL → 選 metric → 選 iterations → 跑
> /trino-research --file q.sql --diagnose-only   # 只產診斷報告，不執行查詢
> /trino-research --file write.sql   # write/DDL：離線 advisory analysis，不執行 SQL
```

一行版（非互動）：

```bash
> /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
> /trino-research --file slow.sql --max-fallbacks 3
```

**MCP 路徑（選配）：** 如果 Step 2 有開 `[mcp.trino] enabled=true`，`/trino-research` 走 MCP 優化路徑（EXPLAIN ANALYZE + table metadata）。MCP 未設定或不可達時會明確報錯，不做 silent fallback；想走直連 driver：`/trino-research --direct`。

**Write-operation analysis：** `--file` 內若是 side-effecting SQL（例如 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`DROP`、`ALTER`、`TRUNCATE`、`RENAME`、`GRANT`、`REVOKE`、`CALL`、`COMMIT`、`ROLLBACK`），會在 MCP/direct 執行面之前改走離線 write-analysis：只做 static analysis + optional LLM advisory，明確不執行 SQL、不跑 EXPLAIN、不 benchmark、不套 `--safe-limit`、不碰 MCP/Trino。Read-only SQL 的 MCP strict 行為不變；MCP 不可達仍報錯，不 silent fallback。v35 限制：default MCP 的互動貼上模式仍可能先做 MCP reachability，MCP-offline write-analysis 請使用 `--file`。

### Step 5 — 拿報告

1. **終端即時輸出** — 每輪迭代的 metric / keep / revert 狀態
2. **Markdown Report** — 一般報告、no-data 靜態報告、directed diagnosis 報告
3. **保證語義正確** — result equivalence guard 逐行比對

報告輸出：

| 情境                                 | 輸出                                                  |
| ------------------------------------ | ----------------------------------------------------- |
| 一般 direct path                     | `./report/trino-research-YYYYMMDD-HHMMSS.md`          |
| 一般 MCP path                        | `trino-research-mcp-YYYYMMDD-HHMMSS.md`               |
| `--diagnose-only` 或長查詢 gate-trip | `./report/trino-research-diagnose-YYYYMMDD-HHMMSS.md` |
| table/schema/catalog no-data         | `./report/trino-research-nodata-YYYYMMDD-HHMMSS.md`   |
| write / DDL analysis-only            | `./report/trino-research-write-analysis-YYYYMMDD-HHMMSS.md` |

---

## 互動指令

| 指令              | 說明                                             |
| ----------------- | ------------------------------------------------ |
| `/trino`          | Trino 連線管理（profiles / test）                |
| `/trino-research` | **Trino SQL 自動優化 + pre-execution diagnosis** |
| `/autoresearch`   | 通用自主迭代 loop                                |
| `/new`            | 新對話                                           |
| `/sessions`       | 列出已儲存的對話                                 |
| `/load <n>`       | 載入對話                                         |
| `/skills`         | 列出所有可用 tools                               |
| `/reasoning`      | 切換 reasoning 等級（disable/low/medium/high）   |
| `/model <name>`   | 切換模型                                         |
| `/exit`           | 結束                                             |

---

## Trino Query Optimization（`/trino-research`）

AI 驅動的 Trino SQL 自動優化。流程不是讓 AI 盲猜改法，而是先做 rule-based gate 和 deterministic diagnosis，再把具體方向餵給 AI：靜態 AST 規則、SQL shape heuristics、EXPLAIN (FORMAT JSON) plan cost、table metadata（MCP path）、runtime peak memory 會先被整理成 rule gate + ranked `OptimizationDirection`，再進入迭代優化。

每輪迭代中，AI 依診斷方向提出優化方案 → 執行驗證 → 通過 guard 才保留。

### 設計原則

1. **AI 回傳完整 SQL**（不依賴 file_patch / diff）
2. **Rule gate first** — 先把 deterministic findings 分成 BLOCK / REWRITE / ADVISE / PASS
3. **Diagnosis first** — 再用 deterministic signals 產生 ranked optimization directions
4. **Result equivalence guard** — 逐行比對查詢結果，確保語義不變
5. **Median verify** — 每個候選 SQL 跑 N 次取中位數，減少 cache 噪音
6. **Iterative accumulation** — 每輪以 current_best 為基準
7. **History trimming** — 只保留最近 4 條對話

### Pre-execution diagnosis

`/trino-research` 會在第一輪優化前組合五種訊號：

| 訊號                 | 來源                     | 用途                                                                                       |
| -------------------- | ------------------------ | ------------------------------------------------------------------------------------------ |
| Static AST findings  | sqlglot rules            | 找 cartesian join、select star、predicate pushdown 等結構問題                              |
| SQL shape heuristics | sqlglot AST              | 偵測多層 heavy CTE、可能重複 raw scan，轉成 materialize-cte-steps / reduce-raw-rescan 方向 |
| Plan cost            | `EXPLAIN (FORMAT JSON)`  | 估 rows / bytes，做 reduce-scan、memory-pressure 等方向排序                                |
| Table metadata       | MCP path                 | 偵測 partition / sort hints，建議 leverage partitioning / ordering                         |
| Peak memory          | baseline runtime metrics | 把 memory pressure 納入目標 metric                                                         |

診斷結果會以 `OptimizationDirection(kind, severity, rationale, evidence, target_metric)` 排序後放進 optimizer prompt。`--direct` 路徑也有同等診斷能力；差別是沒有 MCP metadata。

在 optimizer prompt 的 Trino guide 之前，`/trino-research` 會先插入一段 capped `Rule-based gate`。這不是另一個 LLM 建議，而是 deterministic findings 的 action taxonomy：

| Action    | 代表意思                                                                                    | v31 行為                                                       |
| --------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `BLOCK`   | 高風險語義問題，例如 cartesian join / NULL unsafe equality                                  | 不讓 AI 自動「猜」語義修法；繼續診斷和產報告，不直接 abort CLI |
| `REWRITE` | 高信心 rewrite candidate class，例如 redundant DISTINCT、predicate pushdown、redundant cast | 只建議模型優先處理；v31 不自動改 SQL，仍需 result equivalence  |
| `ADVISE`  | 有幫助但證據較弱或需要環境判斷，例如 CTE step materialization、raw rescan、memory pressure  | 作為 AI context；不把 advisory 當成硬規則                      |
| `PASS`    | 沒有 actionable gate finding                                                                | 不渲染 TUI block，也不污染 prompt                              |

TUI 只顯示一個 compact block，避免把 terminal 變成 rule dump：

```text
  rule gate  block=1  rewrite=2  advise=3
    block    cartesian-join        Do not invent missing join predicates automatically...
    rewrite  predicate-pushdown    Candidate rewrite: push predicate into the CTE/subquery...
    advise   materialize-cte-steps Step materialization is advisory only...
```

Rule gate fail-open：如果上游 diagnostic object malformed，會跳過 gate block，研究流程繼續跑；它不會取代 preflight、execution guard 或 result equivalence。

模型提示也內建 Trino 實戰 guardrails：`WITH`/CTE 在 baseline OSS Trino 不是 cache、深層 CTE + JOIN/GROUP BY 可能造成 plan/stage 爆開；重複 raw/fact/source scan 優先度高於重複讀小型 curated/presum/dimension table；skew、spill、dynamic filtering、CBO stats 與 worker 數限制會被明確納入優化建議。CTAS / materialized view 只會作為 advisory，除非未來開啟 dedicated materialization mode，否則正常 read-only loop 不會自動產生 side-effecting DDL。

### 給 AI 的 Trino 優化方向

`/trino-research` 不只把原 SQL 丟給模型。每次 optimizer prompt 都會帶入以下方向，讓模型優先處理 Trino 真正常見的 bottleneck：

| 方向                        | 觸發線索                                                                               | 給 AI 的要求                                                                                                                   |
| --------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| CTE / `WITH` plan explosion | 3+ chained CTE，且多個 step 有 JOIN / GROUP BY / window / set operation                | 假設 `WITH` 會被 inline，不要當成 cache；優先簡化 plan，必要時建議 managed CTAS / materialized view step 化                    |
| Repeated raw scan           | 同一個 raw / fact / source table 被多次引用                                            | 優先減少 raw scan；不要把小型 curated / presum / dimension table 的重複讀誤判成主要瓶頸                                        |
| Pushdown / pruning          | scan bytes 大、output 小、partition predicate 被 function 包住                         | 把 filter 推到 scan leaf；保留 partition column 原生型別；只選需要的欄位                                                       |
| Join distribution / CBO     | large-large join、build side 過大、stats 缺失                                          | 先建議 stats refresh / `ANALYZE`；broadcast 只適合 filtered build side 可放進每台 worker memory 的情境                         |
| Dynamic filtering           | fact table join filtered dimension                                                     | 保留 selective dimension predicate 和 equi-join key，讓 Trino 能把 runtime filter 推回 probe-side scan                         |
| Skew / spill                | `EXPLAIN ANALYZE` 顯示 per-task input 差距大、blocked time 高、spill 或 peak memory 高 | 先定位 hot key / large build / high-cardinality aggregation，再建議 pre-aggregate、filter NULL/hot keys、縮欄位或改 join shape |
| Worker 數限制               | shared cluster worker 少、scan/shuffle throughput 不足                                 | 可以建議增加 worker / dedicated cluster，但要明確說它不能解 plan depth、per-node memory、skew、spill 的根因                    |

Materialization 是 side-effecting strategy，不是一般 loop 的自動 rewrite。正常 read-only `/trino-research` 只會建議「可考慮 step 化」，不會直接回傳 `CREATE TABLE` / `DROP TABLE` chain；未來若要支援，必須另外開 dedicated materialization mode，明確指定 scratch schema、命名、TTL/cleanup、權限與失敗復原。

### Trino reference links

這些是目前 prompt guidance 對齊的官方 Trino 文件：

| 主題                                                 | Reference                                                                                               |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `WITH` / CTE semantics                               | [Trino SELECT — WITH clause](https://trino.io/docs/current/sql/select.html#with-clause)                 |
| `EXPLAIN` supported types / formats                  | [Trino EXPLAIN](https://trino.io/docs/current/sql/explain.html)                                         |
| Runtime CPU / blocked time / skew statistics         | [Trino EXPLAIN ANALYZE](https://trino.io/docs/current/sql/explain-analyze.html)                         |
| Join ordering, join distribution, CBO stats          | [Trino cost-based optimizations](https://trino.io/docs/current/optimizer/cost-based-optimizations.html) |
| Predicate / projection / aggregation / join pushdown | [Trino pushdown](https://trino.io/docs/current/optimizer/pushdown.html)                                 |
| Dynamic filtering / dynamic partition pruning        | [Trino dynamic filtering](https://trino.io/docs/current/admin/dynamic-filtering.html)                   |
| Materialized view semantics and staleness            | [Trino CREATE MATERIALIZED VIEW](https://trino.io/docs/current/sql/create-materialized-view.html)       |
| Object storage file-system cache                     | [Trino file system cache](https://trino.io/docs/current/object-storage/file-system-cache.html)          |

`WITH (cached = TRUE)` 沒有列入 baseline OSS Trino reference；目前只能視為 fork / vendor / version-specific capability。若公司 Trino 有支援，應先做 capability probe，再把它加入 prompt guidance。

只想看診斷、不跑 baseline query：

```bash
> /trino-research --file query.sql --diagnose-only
```

這會輸出 directed report，成本只到 static analysis + EXPLAIN plan；不跑原 SQL，也不進迭代。

### Guard 機制

| Guard                          | 說明                                                                                        | 失敗時                      |
| ------------------------------ | ------------------------------------------------------------------------------------------- | --------------------------- |
| **Preflight**                  | read-only whitelist + EXPLAIN size estimate + optional `--safe-limit`                       | STOP                        |
| **Rule gate**                  | deterministic findings 分類成 BLOCK / REWRITE / ADVISE / PASS，先渲染 compact TUI 並餵給 AI | FAIL OPEN / PROMPT GUIDANCE |
| **Lint/static**                | SQL 語法與 anti-pattern 分析                                                                | REVERT / REPORT             |
| **Execution**                  | 必須在 Trino 上成功執行                                                                     | REVERT                      |
| **Plan-cost structural guard** | 長查詢模式用 plan signature 過濾結構偏移 candidate                                          | REJECT                      |
| **Result equivalence**         | 優化後結果必須與 baseline 逐行一致                                                          | REVERT                      |

### Long-query handling

`/trino-research` 預設把慢查詢當正常 tuning 對象：baseline wall time 超過 `--long-query-threshold`（預設 60s）時，仍會繼續進入長查詢優化流程。

如果你只想在慢 baseline 後拿 directed report、不跑後續 candidate execution / EXPLAIN ANALYZE，可以明確關掉 long-query tuning：

```bash
> /trino-research --file slow.sql --no-long-query
```

長查詢模式會用 EXPLAIN plan cost 排序 candidate，最後再用 row-equivalence 做 L3 驗證；`--max-fallbacks` 控制候選失敗時最多重試幾個 fallback。進入 iteration 後，candidate / verify run 若超過 baseline wall-time 會視為失敗，不會取代目前 best SQL；MCP path 會 best-effort 設定 Trino `query_max_run_time`，direct path 會用 cursor cancel。長時間執行 baseline、candidate、verify 或 EXPLAIN ANALYZE 時，終端 status 會顯示 `elapsed=<秒數>s`，candidate status 也會顯示 `limit=<秒數>s`；MCP iteration summary 會用 compact block 分行顯示 verdict、metric/delta/elapsed、reason 與 note。

| 參數                     | 說明                                             | 預設        |
| ------------------------ | ------------------------------------------------ | ----------- |
| `--file`                 | SQL 檔案路徑                                     | 互動貼上    |
| `--metric`               | 優化目標 metric                                  | cpu_time_ms |
| `--iterations`           | 最大迭代次數                                     | 5           |
| `--runs`                 | 每次驗證重複跑幾次                               | 3           |
| `--safe-limit`           | 外層包一層 `LIMIT n`                             | off         |
| `--query-timeout`        | 單次 query timeout 秒數                          | 300         |
| `--long-query`           | 允許慢 baseline 進入 tuning（相容旗標）          | on          |
| `--no-long-query`        | 慢 baseline 後只產 directed report，不跑後續迭代 | off         |
| `--long-query-threshold` | 超過幾秒視為長查詢                               | 60          |
| `--max-fallbacks`        | row-equivalence fallback 重試上限                | 3           |
| `--diagnose-only`        | 只產 directed report，不執行原 SQL               | off         |
| `--direct`               | 強制走 local Trino driver，不走 MCP              | off         |

**可選 metric：** `query_time_ms` / `cpu_time_ms` / `wall_time_ms` / `physical_input_bytes` / `processed_rows` / `total_splits` / `peak_memory_bytes`

write/DDL SQL 會忽略 metric / iterations / runs / `--diagnose-only` / `--safe-limit` 的執行語意，直接產 advisory-only write-analysis report。

---

## MCP Trino 整合

透過 MCP（Model Context Protocol）連接 Trino MCP Server，支援 remote query 執行和 autoresearch。

### 設定

```bash
genie setup mcp    # 互動式設定
```

或手動：

```toml
# ~/.genie/config.toml
[mcp.trino]
url = "http://localhost:8811"
enabled = true
timeout = 30
```

### /trino-research 自動路由

當 `[mcp.trino].enabled = true` 時，`/trino-research` 會走 MCP 路徑（baseline 測量、EXPLAIN ANALYZE、metadata 建議都由 MCP server 提供）。MCP 無法連線或未啟用時會明確報錯；不做 silent fallback，避免你以為正在測 MCP、實際卻走 direct driver。

例外：`/trino-research --file write.sql` 若分類為 write/DDL，會在 MCP config / `McpClient` / `list_tools()` 前產生離線 write-analysis report；read-only `--file` 不走這個例外。

強制跑直連模式：

```bash
> /trino-research --direct --file query.sql
```

只跑 MCP/direct 共用的診斷報告：

```bash
> /trino-research --file query.sql --diagnose-only
> /trino-research --direct --file query.sql --diagnose-only
```

---

## Oracle → Trino 遷移

5 個 tools 支援 Oracle SQL 到 Trino 的轉換：

- **transpile** — sqlglot 機械轉換 + AI 補完
- **lookup** — 150+ Oracle 函數對應表
- **limitations** — Trino 限制清單
- **analyze_sp** — Stored procedure 複雜度分析
- **detect_unsupported** — 不支援構造偵測

---

## 如何撰寫 Skill

每個 skill 是 `genie/skills/<name>/` 下的一個目錄：

| 檔案          | 用途                          |
| ------------- | ----------------------------- |
| `SKILL.md`    | Metadata（discovery 的依據）  |
| `__init__.py` | BaseSkill 子類別 + register() |

```python
from genie.core.arg import Arg
from genie.core.registry import BaseSkill

class MyTool(BaseSkill):
    name = "my_tool"
    description = "做什麼事"
    group = "my_group"
    tier = "core"
    args = [Arg(name="input", type="str", description="輸入", required=True)]

    def run(self, input="") -> str:
        return f"result: {input}"

def register(registry) -> None:
    registry.register(MyTool())
```

---

## 檔案結構

```text
genieCLI/
├── genie/
│   ├── cli.py / chat.py / input.py       CLI、REPL、tool dispatch
│   ├── core/
│   │   ├── provider.py / context.py / registry.py
│   │   ├── llm_adapters.py               shared provider → advisory LLM adapter
│   │   └── sql_extraction.py             SQL/CTAS extraction and structural guards
│   ├── output/
│   │   ├── human.py / machine.py
│   │   └── step_trace.py                 ordered step telemetry and renderers
│   ├── providers/                        OpenAI-compatible, Anthropic, TGenie
│   ├── skills/
│   │   ├── mcp_trino/
│   │   │   ├── client.py / research.py   MCP adapter and orchestration
│   │   │   ├── preflight.py              shared six-route state machine / plan-cost core
│   │   │   ├── trino_optimize.py         baseline→decompose→optimize→recompose→verify
│   │   │   ├── critical_path.py          offline structural cost model
│   │   │   ├── p_strategies.py           P1–P9 safety-tiered strategy menu
│   │   │   └── strategy_verify.py        P9 fan-out and evidence coverage
│   │   ├── trino_query/
│   │   │   ├── connection.py / research.py  trino.dbapi direct adapter
│   │   │   ├── plan_signature.py
│   │   │   └── sql_static/               R1–R10 sqlglot rules
│   │   └── oracle2trino/
│   ├── runtime/                          generic autoresearch/checkpoint/journal
│   └── session/
├── tests/                                unit, acceptance, parity and integration tests
├── docs/doc-layer/ARCHITECTURE.md         generated architecture reference
├── project-iterations/genieCLI/           historical ledger/status material
├── .tlv5-*/                              Task Ledger V5 run state and artifacts
└── pyproject.toml
```

---

## 開發與驗證流程

目前工作流程使用 **Task Ledger V5**，不是舊的 Task Ledger V3 hook 流程。V5 run
以 repo-root 的 `.tlv5-<run-name>/state.json` 為狀態來源，並在 `artifacts/` 保存
explore、spec、ticket、develop、review 與 wrap/retro 證據；例如目前 HEAD 的
state-machine safety core 對應 `.tlv5-v62-state-machine-core/`。不要把
`.codex/hooks.json` 或 `.claude/settings.json` 中遺留的 V3 hook 文案當成目前開發流程
或 README 指令。

開始工作時，先讀取相關 V5 `state.json`、producer/review artifacts、
`project-iterations/genieCLI/STATUS.md`，再確認最近 commits 與受影響雙路徑的測試。對
`/trino-research` 的變更尤其應維持：MCP/direct 共用決策與 rule-id 契約、logical SQL
read-only gate、失敗候選不取代 baseline、以及 offline 與 live evidence 的界線。

提交前，依修改範圍執行 focused tests，然後跑完整測試與 whitespace 檢查：

```bash
.venv/bin/python -m pytest <focused-test-file-or-slice> -q
.venv/bin/python -m pytest -q
git diff --check
```

若修改 MCP/direct routing 或 shared pipeline，至少覆蓋 state-machine acceptance、
dual-path rule-id parity、plan-cost core 與相關 pipeline/strategy tests；若修改報告或
步驟顯示，也覆蓋 `test_step_trace.py` 與 evidence-coverage tests。live Trino/LLM 驗證
只有在實際環境可用時才可記為 live evidence；離線 AST 或 mock 測試不能宣稱 row-value
等價或實際加速。

---

## 限制與已知問題

1. **Python 3.9+**：已移除 `match/case`，但部分依賴可能需要較新版本
2. **401 token 過期（TGenie）**：只自動 retry 一次，之後需 `/renew`
3. **Ollama `/v1` 不支援 `think=false`**：已自動切換至 native API
4. **Local model 品質**：qwen3.5:4b 能抓常見問題，複雜語義保持需更大模型
