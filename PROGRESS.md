# genieCLI Progress

## 版本歷程

### Phase 3 — v3.0（2026-03-23）
- **OpenAI-compatible Interface**：支援三種 backend mode（tgenie / openai / anthropic）
- **Ubuntu Launch Script**：`tgenie.sh` 自動偵測 Chrome、裝依賴、啟動 debug mode
- **Debug Mode**：`--debug` / `-d` 印出完整 HTTP request/response
- **Reasoning 修復**：reasoning 開啟時不再 empty response
- **Cline Proxy 相容**：`openaiContentArray: true` + 強制 `stream: true`
- **Code Quality Pass**（PR #8）：3 bug fix + 2 improvement + 1 cleanup
- **Typer CLI 遷移**（PR #9）：argparse → Typer，5 個子指令，v2.0 → v3.0

### Phase 2 — v2.0（2026-03-22）
- Persistent CDP singleton（`skills/_cdp.py`）— session 共用一條 WebSocket
- backendNodeId 點擊（`page_context.py`）— DOM.resolveNode → .click()
- React Input 修復 — 用原生 HTMLInputElement.prototype setter

### Phase 1 — v1.0
- 基礎 CLI chat loop + TGenie backend
- Chrome CDP browser skills（~25 個 tools）
- Session 管理（save/load/list）
- Screenshot → AI vision 分析
- Tool call loop detection

---

## 目前架構

```
genieCLI/
├── main.py              CLI 進入點（Typer framework）
├── api.py               HTTP client（TGenie SSE + OpenAI-compatible）
├── config.py            JSON config 讀寫（~/ai-agent-config.json）
├── session.py           對話歷史管理（sessions/ 目錄）
├── skill_runner.py      Tool call 解析 + system prompt 生成
├── page_context.py      CDP 高階封裝（snapshot/click/type）
├── grab_auth.py         自動抓取 TGenie auth token
├── requirements.txt     Python 依賴（requests, websocket-client, typer）
├── tgenie.bat           Windows 一鍵啟動
├── tgenie.sh            Ubuntu/Linux 一鍵啟動
└── skills/
    ├── __init__.py      ALL_SKILLS 列表（auto-discovery）
    ├── _cdp.py          CDP WebSocket singleton
    ├── _registry.py     Skill auto-discovery registry
    ├── base.py          BaseSkill 介面 + Arg dataclass
    ├── browser.py       ~25 個 CDP browser tools
    ├── context.py       高階 context skills（snapshot/element 系列）
    └── system.py        File tools（read/write/list）
```

## 支援的 AI Backend

| interface | 用途 | Config |
|-----------|------|--------|
| `tgenie` | 公司內部 TGenie gateway（預設） | endpoint + authToken |
| `openai` | 標準 OpenAI / Groq / Ollama / LM Studio | openaiBaseUrl + openaiApiKey |
| `anthropic` | Anthropic format（Cline-style proxy） | 同 openai + system 提取 |

### Cline Proxy 特殊設定
```json
{
  "interface": "openai",
  "openaiContentArray": true,
  "openaiBaseUrl": "http://ai-coding-agent.tsmc.com",
  "defaultModel": "coder"
}
```

## CLI 子指令（v3.0）

| 指令 | 說明 |
|------|------|
| `chat` | 互動式 AI 對話（預設） |
| `sessions` | 列出已儲存的對話 |
| `config` | 顯示目前設定（token 遮罩） |
| `tools` | 列出所有 skill tools |
| `renew` | 重新抓 TGenie auth token |

## 待辦 / 未來方向

- [ ] Orchestrator mode — 自動 retry + error 分類 + 保持 session
- [ ] 更多 skill（例如 terminal 操作、git 操作）
- [ ] SSE streaming 即時顯示（逐字輸出，不是等全部回完）

## 注意事項

- **Python 3.9+**：f-string 裡不能用 `f['key']`（< 3.12 會 SyntaxError），用暫存變數
- **Cline proxy**：必須帶 `stream: true` + content 用 array 格式
- **TGenie**：multipart/form-data 手動拼 boundary，不要改格式（server 敏感）
