# GenieCLI Project Memory

## 基本資訊

- **Repo**: https://github.com/emilyorz/genieCLI
- **維護者**: Emily (emilyorz)
- **語言**: Python 3.10+
- **架構**: Plugin-based，genie/ package
- **版本**: v5.0.0（2026-04-13）

## 產品方向

**GenieCLI 定位：AI-powered Trino Query Tuning CLI**

### 核心功能

1. **Trino query 自動優化**（/trino-research — autoresearch loop）
2. **Oracle → Trino SQL 遷移**（sqlglot + AI 補完）
3. **Trino SQL 靜態分析**（11 rules — Oracle 殘留 + anti-patterns）
4. **MCP Trino 整合**（JSON-RPC 2.0 HTTP client）

### Roadmap

| Phase | 內容                                           | 狀態   |
| ----- | ---------------------------------------------- | ------ |
| 1     | Trino SQL Linter（靜態分析）                   | Done   |
| 2     | Oracle → Trino 轉換器                          | Done   |
| 3     | MCP 接 Trino                                   | Done   |
| 4     | Query Optimizer（autoresearch）                | Done   |
| 5     | v5.0.0 減法（移除 browser/deepwiki）           | Done   |
| Next  | Oracle MCP — data procedure → Trino query 轉換 | 規劃中 |

### 關鍵決策

- **不 fork Goose/Aider/OpenCode** — moat 在 domain logic 不在 agent shell
- **MCP server 選型** — alaturqua/mcp-trino-python
- **v5.0.0 減法** — 移除 browser/（30 tools）+ deepwiki/（3 tools），聚焦 Trino
- **共用 patterns 移至 core** — `sql_patterns.py` 消除 cross-skill import

---

## 當前狀態（2026-04-13）

### 已上線功能

- `genie/` plugin 架構（core/providers/output/runtime/skills/session）
- 7 個 skill packages：trino_query、mcp_trino、oracle2trino、trino_linter、file_ops、git_ops、shell_ops
- `genie setup` 互動式設定 wizard（llm/trino/mcp）
- Autoresearch workflow runtime
- Dual-mode output（HumanSink + MachineSink）
- Provider: OpenAI-compatible、Anthropic、TGenie
- 600+ tests

### 技術棧

- **CLI**: Typer
- **Output**: Rich（HumanSink）、JSON（MachineSink）
- **Database Transpilation**: sqlglot + AI
- **SQL Optimization**: autoresearch iteration engine
- **MCP**: JSON-RPC 2.0 HTTP client
- **Test**: pytest
