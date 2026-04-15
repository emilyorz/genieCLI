# GenieCLI

AI-powered Trino query tuning CLI. 用 LLM 自動優化 Trino SQL，結合靜態分析、自動迭代、result equivalence guard。

支援三種 AI 後端：TGenie gateway（公司內部）、**OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio）、**Anthropic**。

**核心功能：** Trino query 自動優化（autoresearch）、Oracle → Trino SQL 遷移、Trino SQL 靜態分析、MCP Trino 整合。

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

### 前置需求

- Python 3.9+
- Trino cluster（或 Docker container）

### 安裝

```bash
cd genieCLI
python3 -m venv .venv && source .venv/bin/activate   # 建議用 venv，避免污染系統 Python
pip install -e .
pip install trino    # Trino Python client（optional — 只跑 LLM chat 可略）
```

安裝後會多出一個 `genie` 指令。驗證：

```bash
which genie          # → .../bin/genie
genie --help
```

> 若 `genie: command not found`：通常是 venv 沒 activate，或 `pip install -e .` 裝到 user site 但 PATH 沒含 `~/.local/bin`。最穩的方式是進 venv 後再跑。

### 設定

```bash
genie setup          # 互動式設定 wizard
```

或手動編輯 `~/.genie/config.toml`：

#### Ollama（本機 LLM，免費）

```toml
interface = "openai"
openaiApiKey = "ollama"
openaiBaseUrl = "http://localhost:11434/v1"
defaultModel = "qwen3.5:4b"
```

#### OpenAI

```toml
interface = "openai"
openaiApiKey = "sk-..."
openaiBaseUrl = "https://api.openai.com/v1"
defaultModel = "gpt-4o"
```

#### TGenie（公司內部）

```toml
interface = "tgenie"
endpoint = "https://your-ai-gateway.internal.company.com"
authToken = "your-token"
defaultModel = "gemini-2.5-flash"
```

> **Ollama 注意：** 自動使用 native `/api/chat` endpoint（非 `/v1`），以正確支援 `think=false`。

### 啟動

```bash
genie --skills       # 互動模式（含 skills）
genie query.sql      # 送檔案
```

> `genie` 和 `python -m genie` 等價；前者是 `pyproject.toml` 宣告的 entry point，只要安裝完成就能用。

---

## 5 分鐘上手：連 Trino → 測 Query → 拿 Report

### Step 1：設定 LLM + Trino

```bash
genie setup       # 設定 LLM backend
genie setup trino # 設定 Trino 連線
```

或用互動指令：

```bash
genie --skills
> /trino add mytrino
> /trino test
```

### Step 2：跑優化

```bash
genie --skills
> /trino-research
# 1. 貼上 SQL
# 2. 選 metric（預設 cpu_time_ms）
# 3. 設 iterations（預設 5）
# 4. 等它跑完 → 自動產出 report
```

一行搞定：

```bash
> /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
```

### 跑完後你會得到

1. **終端即時輸出** — 每輪迭代的 metric / keep / revert 狀態
2. **Markdown Report**（`trino-research-YYYYMMDD-HHMMSS.md`）
3. **保證語義正確** — result equivalence guard 逐行比對

---

## 互動指令

| 指令              | 說明                                           |
| ----------------- | ---------------------------------------------- |
| `/trino`          | Trino 連線管理（profiles / test）              |
| `/trino-research` | **Trino SQL 自動優化**                         |
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

AI 驅動的 Trino SQL 自動優化。每輪迭代中，AI 提出優化方案 → 執行驗證 → 通過 guard 才保留。

### 設計原則

1. **AI 回傳完整 SQL**（不依賴 file_patch / diff）
2. **Result equivalence guard** — 逐行比對查詢結果，確保語義不變
3. **Median verify** — 每個候選 SQL 跑 N 次取中位數，減少 cache 噪音
4. **Iterative accumulation** — 每輪以 current_best 為基準
5. **History trimming** — 只保留最近 4 條對話

### Guard 機制（三層防護）

| Guard                  | 說明                                | 失敗時 |
| ---------------------- | ----------------------------------- | ------ |
| **Lint**               | SQL 語法分析（lint score ≠ F）      | REVERT |
| **Execution**          | 必須在 Trino 上成功執行             | REVERT |
| **Result equivalence** | 優化後結果必須與 baseline 逐行一致  | REVERT |

| 參數           | 說明               | 預設        |
| -------------- | ------------------ | ----------- |
| `--file`       | SQL 檔案路徑       | 互動貼上    |
| `--metric`     | 優化目標 metric    | cpu_time_ms |
| `--iterations` | 最大迭代次數       | 5           |
| `--runs`       | 每次驗證重複跑幾次 | 3           |

**可選 metric：** `cpu_time_ms` / `wall_time_ms` / `physical_input_bytes` / `processed_rows` / `total_splits`

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

當 `[mcp.trino].enabled = true` 時，`/trino-research` 會**自動**走 MCP 路徑（baseline 測量、EXPLAIN ANALYZE、metadata 建議都由 MCP server 提供）。當 MCP 無法連線或未啟用時，退回 local `trino` Python driver。

強制跑直連模式：

```bash
> /trino-research --direct --file query.sql
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
