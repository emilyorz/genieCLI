# GenieCLI Project Memory

## 基本資訊
- **Repo**: https://github.com/emilyorz/genieCLI
- **維護者**: Emily (emilyorz)
- **語言**: Python 3.10+
- **架構**: Plugin-based，genie/ package

## 當前狀態（2026-03-27）

### main 分支最新進度
- **HEAD**: `21b72bf` — refactor: split cli.py and browser tools (Phase 2 H1+H3) (#13)
- 剛完成 Phase 2 H1+H3 重構（cli.py 149行、browser tools 拆成 5 個子模組）

### 已上线的功能
- `genie/` plugin 架構（core/providers/output/runtime/skills/session）
- 5 個內建 skills：browser、file_ops、git_ops、oracle2trino、shell_ops
- Autoresearch workflow runtime
- Dual-mode output（HumanSink + MachineSink）
- Typer CLI（`python -m genie`）
- Provider: OpenAI、Anthropic、TGenie

### 測試狀態
- `pytest tests/` — 44 passed（/opt/homebrew/bin/python3）

### 待辨事項（下一步）
1. **Phase 2 H4**: cli.py 還可再拆分（bootstrap/prompting/cli_support 已拆出，但 cli.py 149行還可再薄）
2. **Phase 2 H5**: 測試覆蓋率仍可擴大（autoresearch smoke test、browser registration smoke test）
3. **文件更新**: README 需更新架构圖以反映新結構
4. **CI/CD**: 尚未設定 GitHub Actions

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
