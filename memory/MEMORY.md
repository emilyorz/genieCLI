# GenieCLI Project Memory

## 基本資訊

- **Repo**: https://github.com/emilyorz/genieCLI
- **維護者**: Emily (emilyorz)
- **語言**: Python 3.10+
- **架構**: Plugin-based，genie/ package

## 產品方向（2026-03-28 定案）

**GenieCLI 定位：Trino-centric Data Platform Tool**
不做通用 AI CLI，收斂為 TSMC Tooling Team 的 Trino 工具。

### User 痛點（優先序）

1. 自己轉 Oracle → Trino，但寫得不好（最常見）
2. 不知道怎麼優化 Trino query 降低 resource
3. 不熟 Trino 語法，不知道怎麼寫

### Roadmap

詳見 `workflow-notes/2026-03-28-genieCLI-trino-roadmap.md`

| Phase | 內容                                            | 需要 Trino 連線 |
| ----- | ----------------------------------------------- | :-------------: |
| 1     | Trino SQL Linter（靜態分析 + pattern matching） |       ❌        |
| 2     | Oracle → Trino 轉換器加強                       |       ❌        |
| 3     | MCP 接 Trino（mcp-trino-python）                |       ✅        |
| 4     | Query Optimizer（plan-aware）                   |       ✅        |
| 5     | Self-iterating 修正 loop                        |       ✅        |
| 6     | HTTP API + Web UI                               |       ❌        |

### 關鍵決策

- **不 fork Goose/Aider/OpenCode** — moat 在 domain logic 不在 agent shell
- **MCP server 選型** — alaturqua/mcp-trino-python（Python, Apache 2.0）
- **Stored procedure 不做全自動轉換** — Trino 沒有 SP，這是重構問題
- **Self-iterating 用狀態機** — 不做自由 agent loop，validator 主導 LLM 輔助

### Blocker

- Trino 內網連線環境尚未就緒（等人建好），Phase 3+ 被 block

---

## 當前狀態（2026-03-28）

### main 分支最新進度

- **HEAD**: `319a42a` — Phase 1 + Phase 2 完成
- 97 linter+converter tests + 270 原有 tests
- Phase 1: Trino SQL Linter（11 rules, 56 tests）
- Phase 2: Oracle→Trino 結構化輸出 + 共用 pattern catalog（41 tests）

### 已上線的功能

- `genie/` plugin 架構（core/providers/output/runtime/skills/session）
- 5 個內建 skills：browser、file_ops、git_ops、oracle2trino、shell_ops
- Autoresearch workflow runtime
- Dual-mode output（HumanSink + MachineSink）
- Typer CLI（`python -m genie`）
- Provider: OpenAI、Anthropic、TGenie
- chat.py 分離（cli.py 瘦身）
- 58% test coverage

### 待辦事項（下一步）

1. **Phase 3** — MCP 接 Trino（等內網環境就緒）
2. **Tech debt** — linter/converter regex 統一（M1 from Phase 2 review）
3. **CI/CD** — 尚未設定 GitHub Actions
4. **README** — 需更新架構圖 + 產品定位

### 過去重要决策

- Autoresearch workflow 使用 unified diff patch（`path` + `patch`），不是 `old_text`/`new_text`
- `_is_tool_failure()` 集中判斷錯誤，覆蓋 ERROR/Validation error/Wrong args/Tool error/Unknown tool/Patch failed/Error applying patch
- Browser skill 延遲導入（lazy import），CDP 執行時才檢查

### 技術棧

- **CLI**: Typer
- **Output**: Rich（HumanSink）、JSON（MachineSink）
- **Browser Automation**: CDP (Chrome DevTools Protocol)
- **Database Transpilation**: sqlglot + AI
- **Test**: pytest

### 重要檔案

- `genie/cli.py` — Entry point，149行
- `genie/chat.py` — Chat REPL loop，371行
- `genie/core/registry.py` — Skill registry
- `genie/runtime/run_manager.py` — Eval loop，321行
- `workflows/autoresearch.md` — Autoresearch workflow 規格
