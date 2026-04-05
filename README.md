# GenieCLI

Plugin-based AI agent CLI。底層用 Provider Protocol 抽象化多家 LLM backend，上層由 SkillRegistry 動態載入工具，透過 Typer CLI 提供互動介面。

支援三種 AI 後端：TGenie gateway（公司內部）、**OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio 等）、**Anthropic**。

**適用場景：** Oracle → Trino SQL 遷移、**Trino query 自動優化（autoresearch）**、Trino query 靜態分析、瀏覽器自動化、Git 操作、Shell 任務。

**v4.2.0** — 46 個 Python 模組、46 個 tools、6100+ 行 code、420 tests。

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
│  openai.py ────────── │  context.py  — SkillContext (DI)      │
│  anthropic.py   │  │  │  config.py   — 設定讀寫               │
│  base.py        │  │  │  arg.py      — Arg descriptor         │
└─────────────────┘  │  │  tool_call.py — JSON parse 共用       │
                     │  └──────────────┬──────────────────────┘
  Ollama: native ────┘                 │
  /api/chat + think=false    ┌─────────┼────────────┐
                             ▼         ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────┐
    │ browser  │ │ file_ops │ │ oracle2trino │ │ trino_query  │
    │ (CDP)    │ │ git_ops  │ │ trino_linter │ │ (optimize +  │
    │ 30 tools │ │ shell_ops│ │              │ │  research)   │
    └──────────┘ └──────────┘ └──────────────┘ └──────────────┘
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

**Ollama 特殊處理：** 偵測到 Ollama（`localhost:11434`）時，自動切換為 native `/api/chat` endpoint 並帶 `think=false`。原因是 Ollama 的 `/v1/chat/completions` 不支援 `think` 參數，Qwen 3/3.5 模型會默認進入 thinking mode 導致回應極慢（2+ 分鐘 vs 4 秒）。需要 vision/files 時才 fallback 到 `/v1`。

#### Skills（`genie/skills/`）— 46 tools

| Skill           | Tools | 說明                                                          |
| --------------- | ----- | ------------------------------------------------------------- |
| `browser/`      | 30    | Chrome CDP automation（snapshot / click / type / intercept…） |
| `file_ops/`     | 4     | 檔案讀寫、目錄列表、file_patch                                |
| `git_ops/`      | 5     | Git 操作（status / diff / log / checkpoint / restore）        |
| `shell_ops/`    | 1     | Shell 指令執行（whitelisted profiles）                        |
| `oracle2trino/` | 5     | Oracle → Trino SQL 轉換（sqlglot + AI 補完）                  |
| `trino_linter/` | 1     | Trino SQL 靜態分析（11 rules：Oracle 殘留 + anti-patterns）   |
| `trino_query/`  | 2     | Trino query 執行 + **自動優化（trino-research）**             |

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
| `run_manager.py`      | 迭代狀態管理（compare against current_best）          |
| `checkpoint.py`       | Git checkpoint + revert                               |
| `metric.py`           | Metric 提取 + 趨勢比較                                |
| `journal.py`          | TSV journal 記錄（每輪 metric / status / hypothesis） |
| `autoresearch_cli.py` | CLI 互動問答（Goal / Scope / Verify 設定）            |

---

## 快速開始

### 前置需求

- Python 3.9+（已移除 `match/case` 依賴，支援公司內部機器）
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

編輯 `~/.genie/config.toml`：

#### TGenie backend（公司內部，預設）

```toml
interface = "tgenie"
endpoint = "https://your-ai-gateway.internal.company.com"
authToken = "your-token"
defaultModel = "gemini-2.5-flash"
```

#### OpenAI-compatible（OpenAI / Groq / Ollama / LM Studio）

```toml
interface = "openai"
openaiApiKey = "sk-..."
openaiBaseUrl = "https://api.openai.com/v1"
defaultModel = "gpt-4o"
```

#### Ollama（本機 LLM）

```toml
interface = "openai"
openaiApiKey = "ollama"
openaiBaseUrl = "http://localhost:11434/v1"
defaultModel = "qwen3.5:4b"
```

> **注意：** Ollama 模式下會自動使用 native `/api/chat` endpoint（非 `/v1`），以正確支援 `think=false`。

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

| 指令              | 說明                                           |
| ----------------- | ---------------------------------------------- |
| `/new`            | 新對話                                         |
| `/sessions`       | 列出已儲存的對話                               |
| `/load <n>`       | 載入對話                                       |
| `/history`        | 顯示目前對話內容                               |
| `/skills`         | 列出所有可用 tools                             |
| `/clear`          | 清除目前對話                                   |
| `/paste`          | 多行貼上模式（Ctrl-D 送出）                    |
| `/editor`         | 開編輯器輸入                                   |
| `/autoresearch`   | 啟動自主迭代 loop（需 `--skills`）             |
| `/trino`          | Trino 連線管理（profiles / test）              |
| `/trino-research` | **Trino SQL 自動優化**（見下方）               |
| `/reasoning`      | 切換 reasoning 等級（disable/low/medium/high） |
| `/renew`          | 重新抓 auth token（TGenie only）               |
| `/exit`           | 結束（自動儲存對話）                           |

---

## 5 分鐘上手：連 Trino → 測 Query → 拿 Report

從零開始，5 步拿到優化報告。

### Step 1：裝 GenieCLI + 依賴

```bash
git clone https://github.com/emilyorz/genieCLI.git
cd genieCLI
pip install -e .
pip install trino           # Trino Python client
```

### Step 2：裝 Ollama + 拉模型（本機 LLM，免費）

```bash
# macOS
brew install ollama
brew services start ollama

# 拉模型（4B 夠用，16GB RAM 跑得動）
ollama pull qwen3.5:4b
```

> 沒有 Ollama？也可以用 OpenAI API，把 Step 3 的 config 改成 OpenAI 即可。

### Step 3：設定 config

```bash
mkdir -p ~/.genie
cat > ~/.genie/config.toml << 'EOF'
interface = "openai"
openaiApiKey = "ollama"
openaiBaseUrl = "http://localhost:11434/v1"
defaultModel = "qwen3.5:4b"
EOF
```

### Step 4：連 Trino

```bash
python -m genie --skills

# 進入互動模式後：
> /trino add mytrino
  Host [localhost] > your-trino-host.com
  Port [8085] > 8080
  User [trino] > your_username
  Scheme [http] > https
  Catalog [iceberg] > hive
  Schema [warehouse] > your_schema
  Label > Production Trino

> /trino test
  ✓ Connected to https://your-trino-host.com:8080 (Production Trino)
```

或者直接寫 config 檔：

```bash
mkdir -p ~/.config/genie
cat > ~/.config/genie/trino.json << 'EOF'
{
  "active": "mytrino",
  "profiles": {
    "mytrino": {
      "host": "your-trino-host.com",
      "port": 8080,
      "user": "your_username",
      "scheme": "https",
      "catalog": "hive",
      "schema": "your_schema",
      "label": "Production Trino"
    }
  }
}
EOF
```

### Step 5：跑優化，拿 Report

**方法 A — 互動模式（一步步來）：**

```bash
python -m genie --skills
> /trino-research
# 1. 貼上你要優化的 SQL
# 2. 選 metric（預設 cpu_time_ms）
# 3. 設 iterations（預設 5）
# 4. 設 verify runs（預設 3）
# 5. 等它跑完 → 自動產出 report
```

**方法 B — 一行搞定（非互動）：**

```bash
# 把 SQL 存成檔案
cat > my_query.sql << 'EOF'
SELECT ... FROM ... WHERE ...
EOF

# 進入 CLI 後直接帶參數
python -m genie --skills
> /trino-research --file my_query.sql --metric cpu_time_ms --iterations 5 --runs 3
```

**跑完後你會得到：**

1. **終端即時輸出** — 每輪迭代的 metric / keep / revert 狀態
2. **Markdown Report**（`trino-research-YYYYMMDD-HHMMSS.md`）：
   - Summary table（baseline → best → improvement %）
   - 每輪 iteration history
   - Original SQL vs Optimized SQL
3. **保證語義正確** — result equivalence guard 逐行比對查詢結果

### 完整流程圖

```
┌─────────────┐    ┌──────────┐    ┌──────────────┐    ┌──────────┐
│ 1. Install  │───>│ 2. Ollama│───>│ 3. Config    │───>│ 4. Trino │
│ pip install │    │ pull model│   │ ~/.genie/    │    │ /trino   │
└─────────────┘    └──────────┘    └──────────────┘    └────┬─────┘
                                                            │
                                                            ▼
                   ┌──────────┐    ┌──────────────┐    ┌──────────┐
                   │ Report!  │<───│ 5. Optimize  │<───│ Test OK  │
                   │ .md file │    │ /trino-      │    │ /trino   │
                   └──────────┘    │  research    │    │  test    │
                                   └──────────────┘    └──────────┘
```

---

## Trino Query Optimization（`/trino-research`）

AI 驅動的 Trino SQL 自動優化。每輪迭代中，AI 提出優化方案 → 執行驗證 → 通過 guard 才保留。

### 設計原則（v2, 2026-04-04）

1. **AI 回傳完整 SQL**（不依賴 file_patch / diff）
2. **Result equivalence guard** — 逐行比對查詢結果，確保語義不變
3. **Median verify** — 每個候選 SQL 跑 N 次取中位數，減少 cache 噪音
4. **Iterative accumulation** — 每輪以 current_best 為基準，不回到原始 SQL
5. **History trimming** — 只保留最近 4 條對話，避免 local model context 過長

### 使用方式

#### 互動模式

```bash
python -m genie --skills
> /trino-research
# 貼上 SQL → 選 metric → 設定 iterations → 開始
```

#### 非互動模式（CLI 參數）

```bash
> /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
```

| 參數           | 說明               | 預設        |
| -------------- | ------------------ | ----------- |
| `--file`       | SQL 檔案路徑       | 互動貼上    |
| `--metric`     | 優化目標 metric    | cpu_time_ms |
| `--iterations` | 最大迭代次數       | 5           |
| `--runs`       | 每次驗證重複跑幾次 | 3           |

**可選 metric：** `cpu_time_ms` / `wall_time_ms` / `physical_input_bytes` / `processed_rows` / `total_splits`

### Guard 機制（三層防護）

| Guard                  | 說明                                                               | 失敗時 |
| ---------------------- | ------------------------------------------------------------------ | ------ |
| **Lint**               | SQL 必須通過語法分析（lint score ≠ F）                             | REVERT |
| **Execution**          | SQL 必須在 Trino 上成功執行                                        | REVERT |
| **Result equivalence** | 優化後的查詢結果必須與 baseline **逐行一致**（列數、欄數、每格值） | REVERT |

### 測試範例與結果

**環境：** Mac mini M4 16GB, Ollama qwen3.5:4b, Trino in Docker (localhost:8085)

**測試 SQL：**

```sql
SELECT
    e.employee_id,
    e.first_name || ' ' || e.last_name AS full_name,
    COALESCE(e.commission_pct, 0) AS commission,
    CASE
        WHEN e.department_id = 10 THEN 'Admin'
        WHEN e.department_id = 20 THEN 'Marketing'
        WHEN e.department_id = 30 THEN 'IT'
        ELSE 'Other'
    END AS dept_name,
    date_diff('day', e.hire_date, CURRENT_DATE) AS days_employed,
    d.department_name,
    (SELECT COUNT(*) FROM employees_full e2
     WHERE e2.manager_id = e.employee_id) AS direct_reports
FROM employees_full e
LEFT JOIN departments d ON e.department_id = d.department_id
ORDER BY e.salary DESC
FETCH FIRST 100 ROWS ONLY
```

**結果（5 iterations, 3 verify runs each）：**

| #   | Status         | Metric (cpu_time_ms) | Delta | 說明                                           |
| --- | -------------- | -------------------- | ----- | ---------------------------------------------- |
| 1   | exec_failed    | —                    | —     | AI 產生的 SQL 有 column reference error        |
| 2   | worse          | 21.0                 | +0.0  | 沒改善，REVERT                                 |
| 3   | **improved**   | 20.0                 | -1.0  | 小幅改善，KEPT                                 |
| 4   | **improved**   | 13.0                 | -7.0  | CTE + LEFT JOIN 取代 correlated subquery，KEPT |
| 5   | semantic_drift | 15.0                 | +2.0  | 結果比對發現 `direct_reports` 值改變，REVERT   |

**最終：Baseline 21ms → Best 13ms（-38.1%），2/5 kept，結果完全等價。**

**AI 產生的優化 SQL：**

```sql
WITH direct_reports_count AS (
    SELECT manager_id, COUNT(*) AS report_count
    FROM employees_full
    GROUP BY manager_id
)
SELECT
    e.employee_id,
    e.first_name || ' ' || e.last_name AS full_name,
    COALESCE(e.commission_pct, 0) AS commission,
    CASE WHEN e.department_id = 10 THEN 'Admin'
         WHEN e.department_id = 20 THEN 'Marketing'
         WHEN e.department_id = 30 THEN 'IT'
         ELSE 'Other'
    END AS dept_name,
    date_diff('day', e.hire_date, CURRENT_DATE) AS days_employed,
    d.department_name,
    COALESCE(dr.report_count, 0) AS direct_reports
FROM employees_full e
INNER JOIN departments d ON e.department_id = d.department_id
LEFT JOIN direct_reports_count dr ON e.employee_id = dr.manager_id
ORDER BY e.salary DESC
FETCH FIRST 100 ROWS ONLY
```

核心優化：**把 N+1 correlated subquery 改成 CTE + LEFT JOIN**，splits 從 121 降到 86。

### Report 輸出

每次 `/trino-research` 完成後自動產出 markdown report（`trino-research-YYYYMMDD-HHMMSS.md`），包含：

- Summary table（baseline / best / improvement / iterations）
- Iteration history（每輪 status + metric + hypothesis）
- Original SQL vs Optimized SQL

---

## Autoresearch — 通用自主迭代 Loop

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

## Trino 連線管理

```bash
> /trino                    # 顯示所有 profiles + 連線狀態
> /trino use local          # 切換 profile
> /trino add staging        # 新增 profile（互動）
> /trino remove staging     # 移除 profile
> /trino test               # 測試目前連線
```

Profiles 儲存在 `~/.config/genie/trino.json`。

---

## 檔案結構

```
genieCLI/
├── genie/                         主套件（v4.2.0，plugin-based）
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
│   │   ├── openai.py              OpenAI-compatible + Ollama native
│   │   ├── anthropic.py           Anthropic API
│   │   └── base.py                共用 HTTP helpers
│   ├── skills/                    46 個 tools
│   │   ├── browser/               Chrome CDP（30 tools）
│   │   ├── file_ops/              檔案讀寫（4 tools）
│   │   ├── git_ops/               Git 操作（5 tools）
│   │   ├── shell_ops/             Shell 執行（1 tool）
│   │   ├── oracle2trino/          Oracle → Trino 轉換（5 tools）
│   │   ├── trino_linter/          SQL 靜態分析（1 tool, 11 rules）
│   │   └── trino_query/           Trino query 執行 + 自動優化
│   │       ├── __init__.py        QueryMetrics + 執行邏輯
│   │       ├── connection.py      Profile-based 連線管理
│   │       └── research.py        /trino-research 迭代引擎 (v2)
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

### ✅ Phase 4：技術債清理 + Trino 優化（2026-04-03~04）

- 刪除 ~4900 行 legacy monolith code
- Python 3.9 相容（移除 match/case）
- Ollama local LLM 支援（native API + think=false）
- **Trino query 自動優化（/trino-research v2）**
  - AI 回傳完整 SQL（取代 file_patch）
  - Result equivalence guard（逐行比對）
  - Median verify（減少 cache 噪音）
  - Non-interactive CLI 參數模式
  - Markdown report 自動產出

### 規劃中

- **Trino Query Advisor**：Schema introspection + partition hints + query guard
- 詳見 [market research](research/trino-ai-assistant-market-research.md)

---

## 限制與已知問題

1. **Python 3.9+**：已移除 `match/case`，但部分依賴可能需要較新版本
2. **需保持 Chrome 分頁開著**：CDP 只綁定到已開的分頁
3. **Element ID 每次 snapshot 重置**：頁面變動後需重新 `browser_snapshot`
4. **React controlled inputs**：有特別處理，但某些客製化 input library 可能失效
5. **401 token 過期（TGenie）**：只自動 retry 一次，之後需 `/renew`
6. **Ollama `/v1` endpoint 不支援 `think=false`**：已自動切換至 native API，但 vision 需 fallback
7. **Local model 品質**：qwen3.5:4b 能抓到 N+1 correlated subquery 等常見問題，但複雜語義保持需要更大模型
