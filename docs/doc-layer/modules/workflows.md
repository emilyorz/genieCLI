---
covers:
  - "workflows/*.md"
  - "workflows/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

The `workflows/` package provides a markdown-driven workflow definition system for genieCLI. It owns two concerns: (1) the `WorkflowLoader` class that discovers, parses, and loads `.md` files with YAML frontmatter from the workflows directory, and (2) the bundled workflow definitions (currently `autoresearch.md`) that describe agent iteration protocols as structured markdown docs. The loader is the runtime bridge between static workflow docs and the chat loop — it extracts metadata, checks skill prerequisites, and strips frontmatter to produce body text ready for system-prompt injection.

## Exports

**`workflows/__init__.py`** re-exports `WorkflowLoader` as the sole public symbol (`__all__ = ["WorkflowLoader"]`).

**`WorkflowLoader` (loader.py, line 29)**

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(directory: Path \| str \| None = None) -> None` | Initialises loader; defaults to the package directory itself |
| `_parse_frontmatter` | `(text: str) -> tuple[dict, str]` | Splits YAML frontmatter from body; returns `({}, full_text)` on any parse failure — never raises |
| `discover` | `() -> list[dict[str, Any]]` | Glob-scans `*.md` files; returns list of frontmatter dicts augmented with `name`, `description`, `requires`, `file` keys; unreadable files are silently skipped |
| `load` | `(name: str) -> str \| None` | Returns full markdown content (including frontmatter) for `{name}.md`, or `None` if absent |
| `check_requirements` | `(name: str, available_skills: list[str]) -> bool` | Returns `True` iff every skill in the workflow's `requires` list is present in `available_skills`; returns `False` for missing workflow |
| `inject_prompt` | `(name: str) -> str \| None` | Returns the frontmatter-stripped body text for system-prompt injection, or `None` if not found |

**`autoresearch.md`** — the `autoresearch` workflow definition. Frontmatter declares `name: autoresearch`, `description: Autonomous goal-directed iteration loop`, and `requires: [file_patch, git_checkpoint_create, git_checkpoint_restore, command_run, git_status, git_diff, git_log]`. The body is a structured prompt (zh-TW) instructing the agent to make one atomic change per iteration, read prior results before each step, and stop after five consecutive non-improvements.

## Invariants

- **Fail-open discovery**: `_parse_frontmatter` and `discover` must never raise; bad YAML or unreadable files fall back silently. Callers depend on this for startup resilience.
- **Frontmatter defaults**: `discover` always populates `name` (stem fallback), `description` (`""`), and `requires` (`[]`) so callers can access these keys unconditionally.
- **`inject_prompt` strips frontmatter**: callers must not strip it themselves — the returned body is already clean for prompt injection.
- **`check_requirements` returns `False` on missing workflow**: callers should not assume a `True` means the file exists; they should call `load` separately if they need the content.
- **Directory default is the package itself**: `WorkflowLoader()` with no arguments always resolves to `workflows/` relative to `loader.py`, not the caller's cwd. Override via the `directory` argument in tests.
- **Only `*.md` files are scanned**: Python modules (`__init__.py`, `loader.py`) in the same directory are never surfaced by `discover`.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial card — documents WorkflowLoader API and autoresearch workflow definition
