# genieCLI Progress

## 版本歷程

### Phase 4 — v4.1.0（2026-04-03）

- **技術債清理**：刪除 27 個 legacy 檔案（~4900 行 dead code），repo 只剩 `genie/` package
- **Bug Fix**：移除 dead CLI subcommands（Typer callback/options collision）+ 修 screenshot tool chain output
- **Linter 品質**：adversarial review 通過（H1-H3/M1-M3 全確認修好），analyzer 加 debug logging
- **文件更新**：README 架構圖重寫、啟動指令統一為 `python -m genie`

### Phase 3 — v3.0/v4.0.0（2026-03-23 ~ 2026-03-31）

- **核心重構**：949 行 monolith → plugin-based `genie/` package（5741 行，44 個模組）
- **OpenAI-compatible Interface**：支援 TGenie / OpenAI / Anthropic 三種 backend
- **Oracle → Trino**：sqlglot 機械轉換 + AI 補完 + 靜態 linter（11 rules）
- **Autoresearch**：自主迭代引擎（propose → verify → commit/revert loop）
- **Dual-mode output**：HumanSink（Rich）+ MachineSink（JSON）
- **420 tests**，SkillRegistry v2 + clear hooks

### Phase 2 — v2.0（2026-03-22）

- Persistent CDP singleton — session 共用一條 WebSocket
- backendNodeId 點擊 — DOM.resolveNode → .click()
- React Input 修復 — 原生 HTMLInputElement.prototype setter

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
├── genie/                    主套件（44 模組，5741 行）
│   ├── cli.py                Typer CLI 入口
│   ├── chat.py               Chat loop + tool dispatch
│   ├── input.py              互動輸入
│   ├── core/                 核心抽象（6 模組）
│   ├── providers/            LLM 後端（4 模組）
│   ├── skills/               46 tools（6 skill packages）
│   ├── output/               HumanSink / MachineSink
│   ├── runtime/              Autoresearch 引擎
│   └── session/              對話管理
├── tests/                    420 tests
├── grab_auth.py              TGenie auth token
├── pyproject.toml            v4.1.0
└── tgenie.sh / tgenie.bat    一鍵啟動

```

## 統計

| 項目 | 數值 |
|------|------|
| Python 模組 | 44 |
| Code 行數 | 5741 |
| Tools | 46 |
| Tests | 420 |
| Lint Rules | 11 |
| Providers | 3（TGenie / OpenAI / Anthropic） |

## 待辦 / 未來方向

- [ ] Trino Query Advisor — Schema introspection + partition hints + query guard
- [ ] SSE streaming 即時逐字輸出
- [ ] 更多 skills（terminal 操作、Slack 通知）
