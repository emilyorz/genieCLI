# GenieCLI

AI-powered Trino query tuning CLI. 用 LLM 自動優化 Trino SQL，結合靜態分析、EXPLAIN 診斷、自動迭代、result equivalence guard。

支援三種 AI 後端：TGenie gateway（公司內部）、**OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio）、**Anthropic**。

**核心功能：** Trino query 自動優化（autoresearch）、pre-execution directed diagnosis、長查詢迭代跳過診斷報告、Oracle → Trino SQL 遷移、Trino SQL 靜態分析、MCP Trino 整合。

**v5.0.0** — 聚焦 Trino query tuning，移除無關功能（browser automation、deepwiki），共用 pattern catalog 移至 core。

---

## 架構

```
┌──────────────────────────────────────────────────────────────┐
│  CLI Layer                                                   │
│  cli.py (Typer) — 入口 + 子指令路由                          │
│  chat.py — Chat loop + tool call 路由                        │
│  input.py — 互動輸入（多行、補全）                            │
└─────────────────────┬────────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌─────────────────┐     ┌─────────────────────────────────────┐
│  Providers      │     │  Core (Engine)                       │
│                 │     │  registry.py    — SkillRegistry       │
│  tgenie.py      │     │  provider.py    — Provider Protocol   │
│  openai.py ─────────  │  context.py     — SkillContext (DI)   │
│  anthropic.py   │  │  │  config.py      — 設定讀寫            │
│  base.py        │  │  │  sql_patterns.py— 共用 Oracle pattern │
│                 │  │  │  sql_utils.py   — SQL text utilities  │
└─────────────────┘  │  └──────────────┬──────────────────────┘
  Ollama: native ────┘                 │
  /api/chat + think=false    ┌─────────┼────────────┐
                             ▼         ▼            ▼
               ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
               │ oracle2trino │ │ trino_linter │ │ trino_query  │
               │ (5 tools)    │ │ (1 tool,     │ │ (optimize +  │
               │              │ │  11 rules)   │ │  research)   │
               └──────────────┘ └──────────────┘ └──────────────┘
                             │         │            │
                             ▼         ▼            ▼
               ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
               │ mcp_trino    │ │ file_ops     │ │ git_ops      │
               │ (MCP client) │ │ (4 tools)    │ │ (5 tools)    │
               └──────────────┘ └──────────────┘ └──────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Output: HumanSink（Rich 彩色）/ MachineSink（JSON）        │
└─────────────────────────────────────────────────────────────┘
```

### 模組總覽

#### Core（`genie/core/`）— Engine

| 模組              | 說明                                                           |
| ----------------- | -------------------------------------------------------------- |
| `provider.py`     | `Provider` Protocol、`CompletionRequest`、`Delta` dataclass    |
| `registry.py`     | `SkillRegistry`（discover / dispatch）、`BaseSkill` base class |
| `context.py`      | `SkillContext`（DI container：provider、output sink、session） |
| `config.py`       | 設定讀寫，自動補 DEFAULTS                                      |
| `sql_patterns.py` | 共用 Oracle construct catalog（oracle2trino + trino_linter）   |
| `sql_utils.py`    | SQL text utilities（strip comments/strings）                   |
| `arg.py`          | `Arg` descriptor（skill 參數宣告 + 驗證）                      |
| `tool_call.py`    | Tool call JSON 解析 + normalize 共用邏輯                       |

#### Skills（`genie/skills/`）— 可插拔工具

| Skill           | Tools | 說明                                                        |
| --------------- | ----- | ----------------------------------------------------------- |
| `trino_query/`  | 4     | Trino query 執行 + EXPLAIN + schema 查詢 + **自動優化**    |
| `mcp_trino/`    | dynamic | MCP Trino client + autoresearch via MCP server            |
| `oracle2trino/` | 5     | Oracle → Trino SQL 轉換（sqlglot + AI 補完）               |
| `trino_linter/` | 1     | Trino SQL 靜態分析（11 rules：Oracle 殘留 + anti-patterns）|
| `file_ops/`     | 4     | 檔案讀寫、目錄列表、file_patch                              |
| `git_ops/`      | 5     | Git 操作（status / diff / log / checkpoint / restore）      |
| `shell_ops/`    | 1     | Shell 指令執行（whitelisted profiles）                      |

#### Runtime（`genie/runtime/`）— Autoresearch 引擎

| 模組                  | 說明                                                  |
| --------------------- | ----------------------------------------------------- |
| `run_manager.py`      | 迭代狀態管理（compare against current_best）          |
| `checkpoint.py`       | Git checkpoint + revert                               |
| `metric.py`           | Metric 提取 + 趨勢比較                                |
| `journal.py`          | TSV journal 記錄（每輪 metric / status / hypothesis） |
| `autoresearch_cli.py` | CLI 互動問答（Goal / Scope / Verify 設定）            |

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
```

一行版（非互動）：

```bash
> /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
> /trino-research --file slow.sql --long-query --max-fallbacks 3
```

**MCP 路徑（選配）：** 如果 Step 2 有開 `[mcp.trino] enabled=true`，`/trino-research` 走 MCP 優化路徑（EXPLAIN ANALYZE + table metadata）。MCP 未設定或不可達時會明確報錯，不做 silent fallback；想走直連 driver：`/trino-research --direct`。

### Step 5 — 拿報告

1. **終端即時輸出** — 每輪迭代的 metric / keep / revert 狀態
2. **Markdown Report** — 一般報告、no-data 靜態報告、directed diagnosis 報告
3. **保證語義正確** — result equivalence guard 逐行比對

報告輸出：

| 情境 | 輸出 |
| ---- | ---- |
| 一般 direct path | `./report/trino-research-YYYYMMDD-HHMMSS.md` |
| 一般 MCP path | `trino-research-mcp-YYYYMMDD-HHMMSS.md` |
| `--diagnose-only` 或長查詢 gate-trip | `./report/trino-research-diagnose-YYYYMMDD-HHMMSS.md` |
| table/schema/catalog no-data | `./report/trino-research-nodata-YYYYMMDD-HHMMSS.md` |

---

## 互動指令

| 指令              | 說明                                           |
| ----------------- | ---------------------------------------------- |
| `/trino`          | Trino 連線管理（profiles / test）              |
| `/trino-research` | **Trino SQL 自動優化 + pre-execution diagnosis** |
| `/autoresearch`   | 通用自主迭代 loop                              |
| `/new`            | 新對話                                         |
| `/sessions`       | 列出已儲存的對話                               |
| `/load <n>`       | 載入對話                                       |
| `/skills`         | 列出所有可用 tools                             |
| `/reasoning`      | 切換 reasoning 等級（disable/low/medium/high） |
| `/model <name>`   | 切換模型                                       |
| `/exit`           | 結束                                           |

---

## Trino Query Optimization（`/trino-research`）

AI 驅動的 Trino SQL 自動優化。流程不是讓 AI 盲猜改法，而是先做 deterministic diagnosis，再把具體方向餵給 AI：靜態 AST 規則、EXPLAIN (FORMAT JSON) plan cost、table metadata（MCP path）、runtime peak memory 會先被整理成 ranked `OptimizationDirection`，再進入迭代優化。

每輪迭代中，AI 依診斷方向提出優化方案 → 執行驗證 → 通過 guard 才保留。

### 設計原則

1. **AI 回傳完整 SQL**（不依賴 file_patch / diff）
2. **Diagnosis first** — 先用 deterministic signals 產生 ranked optimization directions
3. **Result equivalence guard** — 逐行比對查詢結果，確保語義不變
4. **Median verify** — 每個候選 SQL 跑 N 次取中位數，減少 cache 噪音
5. **Iterative accumulation** — 每輪以 current_best 為基準
6. **History trimming** — 只保留最近 4 條對話

### Pre-execution diagnosis

`/trino-research` 會在第一輪優化前組合四種訊號：

| 訊號 | 來源 | 用途 |
| ---- | ---- | ---- |
| Static AST findings | sqlglot rules | 找 cartesian join、select star、predicate pushdown 等結構問題 |
| Plan cost | `EXPLAIN (FORMAT JSON)` | 估 rows / bytes，做 reduce-scan、memory-pressure 等方向排序 |
| Table metadata | MCP path | 偵測 partition / sort hints，建議 leverage partitioning / ordering |
| Peak memory | baseline runtime metrics | 把 memory pressure 納入目標 metric |

診斷結果會以 `OptimizationDirection(kind, severity, rationale, evidence, target_metric)` 排序後放進 optimizer prompt。`--direct` 路徑也有同等診斷能力；差別是沒有 MCP metadata。

只想看診斷、不跑 baseline query：

```bash
> /trino-research --file query.sql --diagnose-only
```

這會輸出 directed report，成本只到 static analysis + EXPLAIN plan；不跑原 SQL，也不進迭代。

### Guard 機制

| Guard | 說明 | 失敗時 |
| ----- | ---- | ------ |
| **Preflight** | read-only whitelist + EXPLAIN size estimate + optional `--safe-limit` | STOP |
| **Lint/static** | SQL 語法與 anti-pattern 分析 | REVERT / REPORT |
| **Execution** | 必須在 Trino 上成功執行 | REVERT |
| **Plan-cost structural guard** | 長查詢模式用 plan signature 過濾結構偏移 candidate | REJECT |
| **Result equivalence** | 優化後結果必須與 baseline 逐行一致 | REVERT |

### Long-query handling

如果 baseline wall time 超過 `--long-query-threshold`（預設 60s），而你沒有明確加 `--long-query`，工具不會盲目進入 N 輪高成本迭代；它會輸出 directed report，告訴你應該先往哪幾個方向改。這個模式下 baseline 已經量測完成，report 省掉的是後續 candidate execution 與 EXPLAIN ANALYZE。

要明確允許長查詢進入 plan-cost loop：

```bash
> /trino-research --file slow.sql --long-query --max-fallbacks 3
```

長查詢模式會用 EXPLAIN plan cost 排序 candidate，最後再用 row-equivalence 做 L3 驗證；`--max-fallbacks` 控制候選失敗時最多重試幾個 fallback。

| 參數           | 說明               | 預設        |
| -------------- | ------------------ | ----------- |
| `--file`       | SQL 檔案路徑       | 互動貼上    |
| `--metric`     | 優化目標 metric    | cpu_time_ms |
| `--iterations` | 最大迭代次數       | 5           |
| `--runs`       | 每次驗證重複跑幾次 | 3           |
| `--safe-limit` | 外層包一層 `LIMIT n` | off |
| `--query-timeout` | 單次 query timeout 秒數 | 300 |
| `--long-query` | 明確允許慢 baseline 進入迭代 | off |
| `--long-query-threshold` | 超過幾秒視為長查詢 | 60 |
| `--max-fallbacks` | row-equivalence fallback 重試上限 | 3 |
| `--diagnose-only` | 只產 directed report，不執行原 SQL | off |
| `--direct` | 強制走 local Trino driver，不走 MCP | off |

**可選 metric：** `query_time_ms` / `cpu_time_ms` / `wall_time_ms` / `physical_input_bytes` / `processed_rows` / `total_splits` / `peak_memory_bytes`

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

| 檔案            | 用途                          |
| --------------- | ----------------------------- |
| `SKILL.md`      | Metadata（discovery 的依據） |
| `__init__.py`   | BaseSkill 子類別 + register() |

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

```
genieCLI/
├── genie/
│   ├── __main__.py                入口
│   ├── cli.py                     Typer CLI
│   ├── chat.py                    Chat loop
│   ├── input.py                   互動輸入
│   ├── core/                      Engine
│   │   ├── provider.py            Provider Protocol
│   │   ├── registry.py            SkillRegistry + BaseSkill
│   │   ├── context.py             SkillContext (DI)
│   │   ├── config.py              設定讀寫
│   │   ├── sql_patterns.py        共用 Oracle pattern catalog
│   │   ├── sql_utils.py           SQL text utilities
│   │   ├── arg.py                 Arg descriptor
│   │   └── tool_call.py           Tool call JSON 解析
│   ├── providers/                  LLM 後端
│   │   ├── tgenie.py              TGenie gateway
│   │   ├── openai.py              OpenAI-compatible + Ollama native
│   │   ├── anthropic.py           Anthropic API
│   │   └── base.py                共用 HTTP helpers
│   ├── skills/                     可插拔工具
│   │   ├── trino_query/           Trino 執行 + 自動優化
│   │   ├── mcp_trino/             MCP Trino client
│   │   ├── oracle2trino/          Oracle → Trino 轉換
│   │   ├── trino_linter/          SQL 靜態分析（11 rules）
│   │   ├── file_ops/              檔案讀寫
│   │   ├── git_ops/               Git 操作
│   │   └── shell_ops/             Shell 執行
│   ├── runtime/                    Autoresearch 引擎
│   ├── output/                     輸出層
│   └── session/                    對話管理
├── tests/
├── pyproject.toml
└── tgenie.sh / tgenie.bat
```

---

## 限制與已知問題

1. **Python 3.9+**：已移除 `match/case`，但部分依賴可能需要較新版本
2. **401 token 過期（TGenie）**：只自動 retry 一次，之後需 `/renew`
3. **Ollama `/v1` 不支援 `think=false`**：已自動切換至 native API
4. **Local model 品質**：qwen3.5:4b 能抓常見問題，複雜語義保持需更大模型
