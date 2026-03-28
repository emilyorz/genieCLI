# TGenie CLI

透過 CLI 操控瀏覽器的 AI Agent 工具。底層使用 Chrome CDP（Remote Debugging Protocol），上層由 LLM 驅動 Tool Agent，讓你可以用自然語言操控瀏覽器。

支援兩種 AI 後端：公司內部 TGenie gateway（預設）或任何 **OpenAI-compatible API**（OpenAI、Groq、Ollama、LM Studio 等）。

**適用場景：** 自動化網頁操作、資料抓取、Dashboard 讀取、爬蟲、UI 測試。

---

## 架構

```
┌──────────────────────────────────────────────────────────────┐
│  main.py                                                     │
│  CLI chat loop：讀取使用者輸入 → 維護 session history          │
└─────────────────────┬────────────────────────────────────────┘
                      │  user message + history
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  api.py                                                      │
│  HTTP client → AI backend（SSE streaming）                    │
│  送 history + system prompt → 接收 AI 回覆                    │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  skill_runner.py                                             │
│  解析 AI 回覆中的 tool call JSON                              │
│  {"tool": "browser_screenshot", "args": {"filename":"x.png"}}│
└─────────────────────┬────────────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ browser  │ │ system   │ │ context  │
   │  skills  │ │  skills  │ │  skills  │
   │ (CDP)    │ │ (file)   │ │ (CDP)    │
   └────┬─────┘ └──────────┘ └──────────┘
        │  CDP WebSocket
        ▼
┌──────────────────┐
│  Chrome          │
│  (--remote-      │
│   debugging-     │
│   port=9222)    │
└──────────────────┘
```

---

## 快速開始

### 前置需求

- Python 3.10+
- Chrome 開啟 remote debugging：

  ```bash
  # macOS
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-debug

  # Windows
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\ChromeDebug
  ```

- 確認 `~/ai-agent-config.json` 有正確的 endpoint 與 auth token（見下節）。

### 安裝依賴

```bash
pip install -r requirements.txt
```

### 設定

編輯 `~/ai-agent-config.json`（不存在會使用預設值）：

#### TGenie backend（預設）

```json
{
  "interface":        "tgenie",
  "endpoint":         "https://your-ai-gateway.internal.company.com",
  "frontendUrl":      "https://your-frontend.internal.company.com",
  "targetUrlKeyword": "ai-app",
  "cookieDomain":     ".company.com",
  "authToken":        "your-auth-token-here",
  "customHeader":     "",
  "defaultModel":     "gemini-2.5-flash",
  "systemPrompt":     "You are a helpful AI assistant.",
  "cookies":          []
}
```

#### OpenAI-compatible interface

把 `interface` 改成 `openai`，填上 `openaiApiKey` 與 `openaiBaseUrl`，就可以接任何相容端點：

```json
{
  "interface":        "openai",
  "openaiApiKey":     "sk-...",
  "openaiBaseUrl":    "https://api.openai.com/v1",
  "defaultModel":     "gpt-4o",
  "systemPrompt":     "You are a helpful AI assistant."
}
```

常用 `openaiBaseUrl` 對照：

| 服務 | openaiBaseUrl |
|------|--------------|
| OpenAI | `https://api.openai.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Ollama（本機） | `http://localhost:11434/v1` |
| LM Studio（本機） | `http://localhost:1234/v1` |
| 公司內部 Cline proxy | `http://your-internal-server` |

> OpenAI interface 不需要 TGenie auth token，也不需要跑 `grab_auth.py`。

#### Cline-style 內部 proxy

某些公司內部 proxy（如 Cline 使用的 server）要求 user message content 以陣列格式傳送：

```json
{
  "interface":          "openai",
  "openaiApiKey":       "your-key",
  "openaiBaseUrl":      "http://your-internal-proxy",
  "defaultModel":       "coder",
  "openaiContentArray": true
}
```

`openaiContentArray: true` 會把 user message 從純字串改成：
```json
"content": [{"type": "text", "text": "你的訊息"}]
```

不加這個設定的話，內部 proxy 會回 500 錯誤。

#### 欄位說明

| 欄位 | 說明 |
|------|------|
| `interface` | `"tgenie"`（預設）或 `"openai"` |
| `endpoint` | TGenie backend API endpoint |
| `frontendUrl` | TGenie 網頁應用 URL（CDP cookie domain 比對用） |
| `targetUrlKeyword` | Chrome CDP 找 tab 的關鍵字 |
| `cookieDomain` | 抓 cookie 時比對的 domain |
| `authToken` | TGenie Bearer token（可用 `grab_auth.py` 自動抓） |
| `openaiApiKey` | OpenAI API key（或本機 dummy key） |
| `openaiBaseUrl` | OpenAI-compatible endpoint URL |
| `openaiContentArray` | `true` = user content 用陣列格式（Cline-style proxy 需要） |
| `defaultModel` | 預設使用的模型 |
| `systemPrompt` | 系統提示詞 |

> **注意：** config 讀取時會自動以 DEFAULTS 補全缺少的欄位，不需要在 JSON 裡填寫每個 key。

### 取得 Auth Token（TGenie only）

```bash
python grab_auth.py
```

腳本會：
1. 找到 Chrome 中第一個包含 `targetUrlKeyword` 的分頁
2. 在 textarea 輸入測試文字並點擊傳送
3. 攔截並取出 Authorization header
4. 順便把 domain cookie 一起存進 `~/ai-agent-config.json`

> 使用 `interface: openai` 時不需要這個步驟。

### 啟動 CLI

**macOS / Linux（推薦）：**

```bash
# 直接啟動（預設進 chat + skills）
python main.py
./tgenie.sh

# 指定模型與 reasoning
python main.py chat -m gpt-4o -r medium --skills --debug
./tgenie.sh chat -m gemini-2.5-flash -r low --skills
```

**Windows：**
```
tgenie.bat
```
（bat 自動啟動 Chrome debug mode → 抓 token → 進入 CLI）

**子指令：**

```bash
python main.py --help          # 顯示所有子指令
python main.py chat --help     # chat 參數說明
python main.py sessions        # 列出已儲存的對話
python main.py config          # 顯示目前設定
python main.py renew           # 重新抓 auth token
python main.py tools           # 列出所有 skill tools
```

---

## 指令

| 指令 | 說明 |
|------|------|
| `/new` | 新對話 |
| `/sessions` | 列出已儲存的對話 |
| `/load <n>` | 載入對話 |
| `/history` | 顯示目前對話內容 |
| `/skills` | 列出所有可用 tools（需加 `--skills`） |
| `/clear` | 清除目前對話 |
| `/reasoning` | 切換 reasoning 等級（disable/low/medium/high） |
| `/renew` | 重新抓 auth token |
| `/autoresearch` | 啟動自主迭代 loop（需加 `--skills`） |
| `/help` | 顯示說明 |
| `/exit` | 結束（自動儲存對話） |
| `"""` | 進入多行輸入模式 |

---

## Autoresearch — 自主迭代 Loop

讓 AI 自動迭代改善你的程式碼。設定目標和 metric，AI 會反覆嘗試修改 → 驗證 → 保留/回退，直到達標或用完次數。

### 前置條件

- 必須在 **git repo** 內執行（會用 git 做 checkpoint/revert）
- 必須有一個能輸出數字的 **verify 指令**（例如 `pytest --tb=no -q | tail -1`）
- 加 `--skills` 啟動

### 快速開始

```bash
cd your-project
python main.py --skills

# 輸入
/autoresearch
```

接著會問 6 個問題：

```
1. Goal — what to improve
   → 例：Increase test pass rate

2. Scope — file globs (space-separated) [**/*.py]
   → 例：src/**/*.py tests/**/*.py

3. Verify command — shell command whose stdout contains the metric
   → 例：pytest --tb=no -q 2>&1 | tail -1 | grep -oP '\d+'

4. Direction — which is better [higher]
   → higher 或 lower

5. Guard command — must exit 0 to keep change (optional)
   → 例：ruff check .（留空跳過）

6. Max iterations [10]
   → 要跑幾輪
```

### 運作原理

```
Loop（每輪）：
  1. AI 讀目前狀態 + git history + metric 趨勢
  2. AI 提出 hypothesis，用 file_patch 做 ONE atomic 修改
  3. Runtime 自動：
     a. git commit（checkpoint）
     b. 跑 guard command（有設的話）
     c. 跑 verify command → 抽 metric
     d. 比較：improved → 保留 / same or worse → git revert
  4. 回報結果給 AI，進入下一輪
```

### 範例：提升測試通過率

```
Goal: Increase pytest pass count
Scope: src/**/*.py
Verify: pytest --tb=no -q 2>&1 | grep -oP '(\d+) passed' | grep -oP '\d+'
Direction: higher
Guard: ruff check .
Iterations: 15
```

### 範例：縮小 bundle size

```
Goal: Reduce JavaScript bundle size
Scope: src/**/*.js
Verify: npx esbuild src/index.js --bundle --minify | wc -c
Direction: lower
Iterations: 10
```

### 輸出

每輪會顯示：
```
── Iteration 3/10 ──────────────────
[Tool] file_patch (path='src/utils.py', ...)
Hypothesis: 移除未使用的 import 以減少 bundle size
[IMPROVED] metric=45230  delta=-1200.0000
```

結束後印 summary + journal 路徑（`autoresearch_journal.tsv`）。

### 注意事項

- **Ctrl+C** 隨時中斷，會印到目前為止的 summary
- 每輪只改一件事，改壞了自動 revert，不會弄髒你的 repo
- Journal 記錄每輪的 metric / status / hypothesis，方便回顧
- Verify 指令的 stdout 裡必須有數字，metric 取最後一個 float

---

## Available Tools

### Browser Skills（需要 `--skills`）

#### 讀取
| Tool | 說明 |
|------|------|
| `browser_snapshot` | 取互動元素快照（按鈕/輸入/連結）+ 頁面文字摘要，**互動前先執行這個** |
| `browser_get_text` | 取得頁面所有可見文字 |
| `browser_get_element` | 用 CSS selector 抓特定元素內容 |
| `browser_get_numbers` | 抓頁面上所有數值（適用於 Dashboard、圖表） |
| `browser_get_bounding_box` | 取得元素位置與尺寸 |
| `browser_get_local_storage` | 讀 localStorage / sessionStorage |
| `browser_get_dom` | 用 CSS selector / class / text 查 DOM |
| `browser_intercept_xhr` | 攔截 XHR/fetch API 回應（抓 chart 原始資料） |
| `browser_get_url` | 取得目前 URL 與標題 |
| `browser_list_tabs` | 列出所有開的分頁 |
| `browser_screenshot` | 全頁截圖 |
| `browser_screenshot_element` | 只截圖特定元素 |

#### 互動
| Tool | 說明 |
|------|------|
| `browser_click` | 點擊（CSS selector 或 x,y 座標） |
| `browser_click_element` | 用 `browser_snapshot` 的 element ID 點擊 |
| `browser_double_click` | 雙擊 |
| `browser_right_click` | 右鍵點擊 |
| `browser_type` | 輸入文字 |
| `browser_type_element` | 用 element ID 輸入 |
| `browser_select` | 選取下拉選項 |
| `browser_checkbox` | 勾選/取消勾選 checkbox 或 radio |
| `browser_keyboard` | 按鍵盤按鍵（Enter、Tab、ctrl+a 等） |
| `browser_hover` | Hover 觸發 tooltip / 選單 |
| `browser_mouse_sweep` | 在 chart 上來回移動抓 tooltip 數值 |
| `browser_drag` | 拖曳（適用於 sliders、drag-and-drop） |
| `browser_scroll` | 滾動頁面或特定元素 |
| `browser_wait` | 等待元素出現或消失 |
| `browser_handle_dialog` | 接受或拒絕 alert/confirm/prompt |
| `browser_execute_js` | 執行任意 JavaScript |

#### 分頁管理
| Tool | 說明 |
|------|------|
| `browser_navigate` | 在新分頁開啟 URL |
| `browser_switch_tab` | 切換分頁（用 index 或 URL 關鍵字） |

### File Skills

| Tool | 說明 |
|------|------|
| `read_file` | 讀取檔案內容 |
| `write_file` | 寫入檔案 |
| `list_files` | 列出目錄內容 |

---

## Workflow 範例

### 抓 Dashboard 數值

```
You > 幫我讀這個頁面的所有數字

AI    → browser_snapshot  (取得元素)
AI    → browser_get_numbers  (取出數值)
AI    → [回覆你]
```

### 操作表單

```
You > 登入這個頁面，帳號是 test@test.com

AI    → browser_snapshot
AI    → browser_type_element(element_id=2, text="test@test.com")
AI    → browser_snapshot  (ID 可能變動，需重新取得)
AI    → browser_click_element(element_id=5)
AI    → [等待登入結果]
```

### 抓 Chart 原始資料

```
You > 抓這個圖表的原始數字

AI    → browser_mouse_sweep(selector=".chart", steps=20)
AI    → [分析 tooltips，回傳數值]
      或
AI    → browser_intercept_xhr(url_keyword="api/data", action_js="")
AI    → [攔截並回傳 API 回應]
```

---

## 檔案結構

```
genieCLI/
├── main.py              CLI 進入點（Typer framework）
├── api.py               HTTP client（TGenie SSE + OpenAI-compatible）
├── config.py            設定讀寫（自動補 DEFAULTS）
├── session.py           對話歷史管理
├── skill_runner.py      Tool call 解析與路由
├── page_context.py      CDP 高階封裝（snapshot/click/type）
├── grab_auth.py         自動抓取 auth token（TGenie only）
├── requirements.txt     Python 依賴
├── tgenie.bat           Windows 一鍵啟動
├── tgenie.sh            Ubuntu/Linux 一鍵啟動
└── skills/
    ├── __init__.py      ALL_SKILLS 列表
    ├── base.py          BaseSkill 介面
    ├── browser.py       ~25 個 CDP browser tools
    ├── context.py       高階 CDP tools（snapshot/element 系列）
    └── system.py        檔案工具
```

## Roadmap

### Trino AI Query Advisor（規劃中）

基於 [market research](research/trino-ai-assistant-market-research.md)（2026-03-23），未來計畫在此 repo 加入 Trino 查詢助手相關 skill。

**動機：** 現有 Text-to-SQL 工具都是通用實作，沒有人針對 Trino dialect 做深度整合。  
**方向：**
- Trino-native SQL 生成（懂 partition pruning、Iceberg、connector 限制）
- Schema auto-introspection（catalog/schema/table/column 自動注入 context）
- Query guard（送出前自動 EXPLAIN，估算掃描量）
- Partition hints（主動建議加 partition filter 避免全表掃描）

**競品分析：** 目前最接近的是 [`txn2/mcp-trino`](https://github.com/txn2/mcp-trino)（MCP server），但無 advisor 邏輯。詳見 research 文件。

---

## 限制與已知問題

1. **需保持 Chrome 分頁開著** — CDP 只綁定到已開的分頁，關掉就斷線
2. **Element ID 每次 snapshot 都會重置** — 頁面變動後需重新 `browser_snapshot`
3. **React controlled inputs** — 有特別處理，但某些客製化 input library 可能失效
4. **401 token 過期（TGenie）** — 只會自動 retry 一次，之後需手動 `/renew`
5. **Reasoning mode** — 開啟 reasoning 時 server 可能只回 reasoning tokens 而非最終答案；CLI 會自動 fallback，但部分 model/endpoint 組合仍可能有異常
