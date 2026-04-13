# genieCLI Progress

## 版本歷程

### Phase 5 — v5.0.0（2026-04-13）

- **減法重構**：聚焦 Trino query tuning，移除 browser/（1,543 行, 30 tools）+ deepwiki/（212 行）
- **架構整理**：共用 Oracle patterns 移至 `core/sql_patterns.py`，消除 cross-skill import
- **Setup Wizard**：`genie setup [llm|trino|mcp]` 互動式設定
- **文件更新**：README / architecture.md / CLI help 全面改為 Trino 導向
- **依賴清理**：移除 websocket-client（browser 專用），requirements.txt 同步
- **Shell scripts**：Chrome 啟動改為 TGenie-only conditional（非強制）
- **新增 18 個 regression tests**，604 tests pass

### Phase 4 — v4.1.0（2026-04-03）

- **技術債清理**：刪除 27 個 legacy 檔案（~4900 行 dead code）
- **Bug Fix**：移除 dead CLI subcommands + 修 tool chain output
- **Linter 品質**：adversarial review 通過，analyzer 加 debug logging
- **MCP Trino**：MCP client + autoresearch enhancement

### Phase 3 — v3.0/v4.0.0（2026-03-23 ~ 2026-03-31）

- **核心重構**：949 行 monolith → plugin-based `genie/` package
- **OpenAI-compatible Interface**：支援 TGenie / OpenAI / Anthropic 三種 backend
- **Oracle → Trino**：sqlglot 機械轉換 + AI 補完 + 靜態 linter（11 rules）
- **Autoresearch**：自主迭代引擎（propose → verify → commit/revert loop）
- **Dual-mode output**：HumanSink（Rich）+ MachineSink（JSON）

### Phase 1-2 — v1.0~v2.0（2026-03-22 以前）

- 基礎 CLI chat loop + TGenie backend
- Browser CDP skills（已在 v5.0.0 移除）
- Session 管理、Screenshot vision 分析

---

## 目前架構（v5.0.0）

```
genieCLI/
├── genie/
│   ├── cli.py                Typer CLI 入口
│   ├── chat.py               Chat loop + tool dispatch
│   ├── setup_wizard.py       互動式設定 wizard
│   ├── core/                 Engine（8 模組）
│   │   ├── sql_patterns.py   共用 Oracle pattern catalog
│   │   └── ...
│   ├── providers/            LLM 後端（3 providers）
│   ├── skills/               ~20 tools（7 skill packages）
│   │   ├── trino_query/      Trino 執行 + 自動優化
│   │   ├── mcp_trino/        MCP Trino client
│   │   ├── oracle2trino/     Oracle → Trino 轉換
│   │   ├── trino_linter/     SQL 靜態分析（11 rules）
│   │   ├── file_ops/         檔案讀寫
│   │   ├── git_ops/          Git 操作
│   │   └── shell_ops/        Shell 執行
│   ├── runtime/              Autoresearch 引擎
│   ├── output/               HumanSink / MachineSink
│   └── session/              對話管理
├── tests/                    600+ tests
├── pyproject.toml            v5.0.0
└── tgenie.sh / tgenie.bat    啟動腳本
```

## 待辦 / 未來方向

- [ ] Oracle MCP 整合 — data procedure → Trino query 轉換迭代
- [ ] Trino Query Advisor — Schema introspection + partition hints
- [ ] SSE streaming 即時逐字輸出
