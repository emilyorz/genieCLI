# TGenie CLI

透過 CLI 操控瀏覽器的 AI Agent 工具。底層使用 Chrome CDP（Remote Debugging Protocol），上層由 LLM（Gemini）驅動 Tool Agent，讓你可以用自然語言操控瀏覽器。

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

- Python 3.9+
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

```json
{
  "endpoint":          "https://your-ai-gateway.internal.company.com",
  "frontendUrl":      "https://your-frontend.internal.company.com",
  "targetUrlKeyword": "ai-app",
  "cookieDomain":     ".company.com",
  "authToken":        "your-auth-token-here",
  "customHeader":     "",
  "defaultModel":     "gemini-2.5-flash",
  "systemPrompt":     "You are a helpful AI assistant.",
  "cookies": []
}
```

| 欄位 | 說明 |
|------|------|
| `endpoint` | AI backend 的 API endpoint（必需） |
| `frontendUrl` | 你的 AI 網頁應用 URL（用於 CDP cookie domain 比對） |
| `targetUrlKeyword` | Chrome CDP 找 tab 的關鍵字（抓包含這個關鍵字的 tab） |
| `cookieDomain` | 抓 cookie 時要比對的 domain |
| `authToken` | Bearer token（可直接填或用 `grab_auth.py` 自動抓） |
| `defaultModel` | 預設使用的模型 |
| `systemPrompt` | 系統提示詞 |

> **注意：** config 讀取時會自動以 DEFAULTS 補全缺少的欄位，不需要在 JSON 裡填寫每個 key。

### 取得 Auth Token

```bash
python grab_auth.py
```

腳本會：
1. 找到 Chrome 中第一個包含 `targetUrlKeyword` 的分頁
2. 在 textarea 輸入測試文字並點擊傳送
3. 攔截並取出 Authorization header
4. 順便把 domain cookie 一起存進 `~/ai-agent-config.json`

### 啟動 CLI

```bash
python main.py
```

加 `--skills` 啟用 browser/file tools：

```bash
python main.py --skills
```

指定模型與 reasoning 等級：

```bash
python main.py -m gemini-2.0-pro -r medium
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
| `/help` | 顯示說明 |
| `/exit` | 結束（自動儲存對話） |
| `"""` | 進入多行輸入模式 |

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
├── main.py              CLI 進入點
├── api.py               HTTP / SSE client（勿動）
├── config.py            設定讀寫（自動補 DEFAULTS）
├── session.py           對話歷史管理
├── skill_runner.py      Tool call 解析與路由
├── page_context.py      CDP 高階封裝（snapshot/click/type）
├── grab_auth.py         自動抓取 auth token 的腳本（勿動）
├── requirements.txt     Python 依賴
└── skills/
    ├── __init__.py      ALL_SKILLS 列表
    ├── base.py          BaseSkill 介面
    ├── browser.py       ~25 個 CDP browser tools
    ├── context.py       高階 CDP tools（snapshot/element 系列）
    └── system.py        檔案工具
```

## 限制與已知問題

1. **需保持 Chrome 分頁開著** — CDP 只綁定到已開的分頁，關掉就斷線
2. **Element ID 每次 snapshot 都會重置** — 頁面變動後需重新 `browser_snapshot`
3. **React controlled inputs** — 有特別處理，但某些客製化 input library 可能失效
4. **401 token 過期** — 只會自動 retry 一次，之後需手動 `/renew`
5. **SSE streaming** — 使用 streaming mode，回覆會逐字出現
