# GenieCLI

Plugin-based AI agent CLI。底層用 Provider Protocol 抽象化多家 LLM backend，上層由 SkillRegistry 動態載入工具，透過 Typer CLI 提供互動介面。

支援三種 AI 後端：TGenie gateway（公司內部）、**OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio 等）、**Anthropic**。

**適用場景：** Oracle → Trino SQL 遷移、Trino query 靜態分析、瀏覽器自動化、自主迭代 (Autoresearch)、Git 操作、Shell 任務。

**v4.1.0** — 44 個 Python 模組、46 個 tools、5741 行 code、420 tests。

---

## 架構

### 整體分層

```
┌──────────────────────────────────────────────────────────────┐
│  CLI Layer                                                   │
│  genie/cli.py (Typer) — 入口 + 子指令路由                     │
│  genie/chat.py — Chat loop + tool call 路由                   │
│  genie/input.py — 互動輸入（多行、補全）                       │
└─────────────────────┬────────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌─────────────────┐     ┌─────────────────────────────────────┐
│  Providers      │     │  Core                                │
│                 │     │  registry.py — SkillRegistry          │
│  tgenie.py      │     │  provider.py — Provider Protocol      │
│  openai.py      │     │  context.py  — SkillContext (DI)      │
│  anthropic.py   │     │  config.py   — 設定讀寫               │
│  base.py        │     │  arg.py      — Arg descriptor         │
└─────────────────┘     │  tool_call.py — JSON parse 共用       │
                        └──────────────┬──────────────────────┘
                                       │
          ┌────────────┬───────────────┼────────────┐
          ▼            ▼               ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────┐
    │ browser  │ │ file_ops │ │ oracle2trino │ │ runtime  │
    │ (CDP)    │ │ git_ops  │ │ trino_linter │ │ (auto-   │
    │ 30 tools │ │ shell_ops│ │              │ │ research)│
    └──────────┘ └──────────┘ └──────────────┘ └──────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  Output Layer                                               │
│  HumanSink（Rich 彩色）/ MachineSink（JSON）                 │
└─────────────────────────────────────────────────────────────┘
```

### 模組總覽

#### Core（`genie/core/`）

| 模組           | 說明                                                           |
| -------------- | -------------------------------------------------------------- |
| `provider.py`  | `Provider` Protocol、`CompletionRequest`、`Delta` dataclass    |
| `registry.py`  | `SkillRegistry`（discover / dispatch）、`BaseSkill` base class |
| `context.py`   | `SkillContext`（DI container：provider、output sink、session） |
| `config.py`    | 設定讀寫，自動補 DEFAULTS                                      |
| `arg.py`       | `Arg` descriptor（skill 參數宣告 + 驗證）                      |
| `tool_call.py` | Tool call JSON 解析 + normalize 共用邏輯                       |

#### Providers（`genie/providers/`）

| 模組           | 說明                                                    |
| -------------- | ------------------------------------------------------- |
| `tgenie.py`    | 公司內部 TGenie gateway（SSE streaming）                |
| `openai.py`    | OpenAI-compatible（OpenAI / Groq / Ollama / LM Studio） |
| `anthropic.py` | Anthropic API                                           |
| `base.py`      | 共用 HTTP helpers                                       |

#### Skills（`genie/skills/`）— 46 tools

| Skill           | Tools | 說明                                                              |
| --------------- | ----- | ----------------------------------------------------------------- |
| `browser/`      | 30    | Chrome CDP automation（snapshot / click / type / intercept…）     |
| `file_ops/`     | 4     | 檔案讀寫、目錄列表、file_patch                                    |
| `git_ops/`      | 5     | Git 操作（status / diff / log / checkpoint / restore）            |
| `shell_ops/`    | 1     | Shell 指令執行（whitelisted profiles）                            |
| `oracle2trino/` | 5     | Oracle → Trino SQL 轉換（sqlglot + AI 補完）                     |
| `trino_linter/` | 1     | Trino SQL 靜態分析（11 rules：Oracle 殘留 + anti-patterns）      |

#### Output（`genie/output/`）

| 模組         | 說明                                                        |
| ------------ | ----------------------------------------------------------- |
| `human.py`   | `HumanSink`：Rich 彩色輸出，適合互動模式                    |
| `machine.py` | `MachineSink`：newline-delimited JSON，適合管線 / scripting |

#### Runtime（`genie/runtime/`）

Autoresearch 自主迭代引擎，作為 plugin 載入，不耦合 core。

| 模組                  | 說明                                                  |
| --------------------- | ----------------------------------------------------- |
| `eval_loop.py`        | 主迭代 loop（AI propose → verify → commit/revert）    |
| `run_manager.py`      | 迭代狀態管理                                          |
| `checkpoint.py`       | Git checkpoint + revert                               |
| `metric.py`           | Metric 提取 + 趨勢比較                                |
| `journal.py`          | TSV journal 記錄（每輪 metric / status / hypothesis） |
| `autoresearch_cli.py` | CLI 互動問答（Goal / Scope / Verify 設定）            |

---

## 快速開始

### 前置需求

- Python 3.10+（`match/case` syntax）
- Chrome 開啟 remote debugging（browser skills 需要）：

  ```bash
  # macOS
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

  # Windows
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\ChromeDebug
  ```

### 安裝

```bash
cd genieCLI
pip install -e .          # 或 pip install -r requirements.txt
```

### 設定

編輯 `~/ai-agent-config.json`：

#### TGenie backend（公司內部，預設）

```json
{
  "interface": "tgenie",
  "endpoint": "https://your-ai-gateway.internal.company.com",
  "authToken": "your-token",
  "defaultModel": "gemini-2.5-flash"
}
```

#### OpenAI-compatible（OpenAI / Groq / Ollama / LM Studio）

```json
{
  "interface": "openai",
  "openaiApiKey": "sk-...",
  "openaiBaseUrl": "https://api.openai.com/v1",
  "defaultModel": "gpt-4o"
}
```

| 服務              | openaiBaseUrl                    |
| ----------------- | -------------------------------- |
| OpenAI            | `https://api.openai.com/v1`      |
| Groq              | `https://api.groq.com/openai/v1` |
| Ollama（本機）    | `http://localhost:11434/v1`      |
| LM Studio（本機） | `http://localhost:1234/v1`       |

### 啟動

```bash
# 互動模式（預設 chat + skills）
python -m genie

# 指定模型與 reasoning
python -m genie chat -m gpt-4o -r medium --skills --debug

# 送檔案（非互動）
python -m genie query.sql

# Pipe
cat query.sql | python -m genie

# Linux/Ubuntu 一鍵啟動
./tgenie.sh
```

### CLI 參數

```bash
python -m genie --help               # 所有參數
python -m genie --json tools         # JSON 輸出所有 tools
python -m genie sessions             # 列出已儲存的對話
python -m genie config               # 顯示目前設定
```

### 互動指令

| 指令            | 說明                                           |
| --------------- | ---------------------------------------------- |
| `/new`          | 新對話                                         |
| `/sessions`     | 列出已儲存的對話                               |
| `/load <n>`     | 載入對話                                       |
| `/history`      | 顯示目前對話內容                               |
| `/skills`       | 列出所有可用 tools                             |
| `/clear`        | 清除目前對話                                   |
| `/paste`        | 多行貼上模式（Ctrl-D 送出）                    |
| `/editor`       | 開編輯器輸入                                   |
| `/autoresearch` | 啟動自主迭代 loop（需 `--skills`）             |
| `/reasoning`    | 切換 reasoning 等級（disable/low/medium/high） |
| `/renew`        | 重新抓 auth token（TGenie only）               |
| `/exit`         | 結束（自動儲存對話）                           |

---

## Autoresearch — 自主迭代 Loop

讓 AI 自動迭代改善程式碼。設定目標和 metric，AI 會反覆嘗試修改 → 驗證 → 保留/回退。

### 前置條件

- 必須在 **git repo** 內執行
- 必須有能輸出數字的 **verify 指令**
- 加 `--skills` 啟動

### 使用

```bash
python -m genie --skills
> /autoresearch
```

會問 6 個問題：Goal / Scope / Verify command / Direction / Guard command / Max iterations。

### 範例

```
Goal: Increase pytest pass count
Verify: pytest --tb=no -q 2>&1 | grep -oP '(\d+) passed' | grep -oP '\d+'
Direction: higher
Guard: ruff check .
Iterations: 15
```

---

## 檔案結構

```
genieCLI/
├── genie/                         主套件（v4.1.0，plugin-based）
│   ├── __main__.py                python -m genie 入口
│   ├── cli.py                     Typer CLI（子指令路由）
│   ├── chat.py                    Chat loop + tool call dispatch
│   ├── input.py                   互動輸入處理
│   ├── core/                      核心抽象
│   │   ├── provider.py            Provider Protocol
│   │   ├── registry.py            SkillRegistry + BaseSkill
│   │   ├── context.py             SkillContext (DI)
│   │   ├── config.py              設定讀寫
│   │   ├── arg.py                 Arg descriptor
│   │   └── tool_call.py           Tool call JSON 解析
│   ├── providers/                 LLM 後端
│   │   ├── tgenie.py              TGenie gateway
│   │   ├── openai.py              OpenAI-compatible
│   │   ├── anthropic.py           Anthropic API
│   │   └── base.py                共用 HTTP helpers
│   ├── skills/                    46 個 tools
│   │   ├── browser/               Chrome CDP（30 tools）
│   │   │   ├── tools.py           Tool 定義
│   │   │   ├── page_context.py    高階 CDP 封裝
│   │   │   └── cdp.py             WebSocket singleton
│   │   ├── file_ops/              檔案讀寫（4 tools）
│   │   ├── git_ops/               Git 操作（5 tools）
│   │   ├── shell_ops/             Shell 執行（1 tool）
│   │   ├── oracle2trino/          Oracle → Trino 轉換（5 tools）
│   │   │   ├── patterns.py        共用 pattern catalog
│   │   │   ├── models.py          ConversionResult
│   │   │   ├── sql_utils.py       SQL 工具函式
│   │   │   └── data/              函數對照表 YAML
│   │   └── trino_linter/          SQL 靜態分析（1 tool, 11 rules）
│   │       ├── analyzer.py        Linter 主邏輯
│   │       └── rules.py           Lint rules
│   ├── output/                    輸出層
│   │   ├── human.py               HumanSink（Rich）
│   │   └── machine.py             MachineSink（JSON）
│   ├── runtime/                   Autoresearch 引擎
│   │   ├── eval_loop.py           迭代 loop
│   │   ├── run_manager.py         狀態管理
│   │   ├── checkpoint.py          Git checkpoint
│   │   ├── metric.py              Metric 提取
│   │   ├── journal.py             TSV journal
│   │   └── autoresearch_cli.py    CLI 問答
│   └── session/                   對話管理
│       └── manager.py             Session CRUD
├── tests/                         420 tests
├── grab_auth.py                   Auth token 抓取（TGenie only）
├── pyproject.toml                 套件設定
├── requirements.txt               Python 依賴
├── tgenie.sh                      Linux 一鍵啟動
└── tgenie.bat                     Windows 一鍵啟動
```

---

## Roadmap

### ✅ Phase 1-3：Core + Oracle → Trino Migration

- Plugin-based 架構（Provider Protocol + SkillRegistry + Dual-mode output）
- Oracle SQL → Trino 機械轉換（sqlglot）+ AI 補完
- Trino 靜態 linter（11 rules，Oracle 殘留 + anti-patterns）
- Linter ↔ Converter 共用 pattern catalog
- 三種 AI backend + Autoresearch 引擎

### ✅ Phase 4：技術債清理（2026-04-03）

- 刪除 ~4900 行 legacy monolith code（main.py / api.py / skills/ / runtime/ 舊副本）
- 修 dead CLI subcommands + screenshot tool chain bug
- Linter adversarial review 通過

### 規劃中

- **Trino Query Advisor**：Schema introspection + partition hints + query guard
- 詳見 [market research](research/trino-ai-assistant-market-research.md)

---

## 限制與已知問題

1. **Python 3.10+**：`genie/chat.py` 使用 `match/case` 語法
2. **需保持 Chrome 分頁開著**：CDP 只綁定到已開的分頁
3. **Element ID 每次 snapshot 重置**：頁面變動後需重新 `browser_snapshot`
4. **React controlled inputs**：有特別處理，但某些客製化 input library 可能失效
5. **401 token 過期（TGenie）**：只自動 retry 一次，之後需 `/renew`
