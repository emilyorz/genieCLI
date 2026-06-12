---
covers:
  - "genie/runtime/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

`genie/runtime` owns the autonomous iteration loop that powers the `/autoresearch`
command. It provides four tightly coupled components: a git-based checkpoint layer
for safe revert (`checkpoint.py`), a shell-command metric extractor with directional
comparison (`metric.py`), a TSV journal for per-iteration record-keeping
(`journal.py`), and a stateful orchestrator (`run_manager.py`) that sequences
baseline → hypothesis → guard → verify → keep/revert across N iterations.
The interactive CLI entry point (`autoresearch_cli.py`) collects user config, builds
a session, and drives `RunManager` in a prompt-response loop against the active LLM
provider. No component in this package modifies global config or touches the skills
registry directly.

## Exports

### autoresearch_cli.py

- `_run_autoresearch(provider, cfg, model, reasoning, output, build_prompt) -> None`
  — Interactive setup wizard + main loop; reads goal/scope/verify/direction/guard/
  max_iterations from stdin, then drives `RunManager` step-by-step with LLM
  tool-call replies. Called by `genie/chat.py` when the `/autoresearch` command is
  detected.

### checkpoint.py

- `git_is_repo(cwd) -> bool` — True if cwd is inside a git repo.
- `git_is_clean(cwd) -> bool` — True when working tree has no uncommitted changes.
- `checkpoint_create(label, cwd) -> dict` — Stages all changes and commits them,
  recording `original_head` before the commit so a restore can undo it. Returns
  `{"label", "original_head", "commit_sha", "timestamp", "cwd"}` or `{"error": ...}`.
- `checkpoint_restore(checkpoint_info, cwd) -> dict` — Hard-resets to
  `checkpoint_info["original_head"]`, discarding the checkpoint commit. Returns
  `{"ok": True, "restored_head": str}` or `{"error": ...}`.

### journal.py

- `JournalWriter(path)` — Opens or creates a TSV file with columns
  `iteration | commit | metric | delta | guard | status | description`.
  - `.write_baseline(metric, commit, description)` — Writes the iteration-0 row.
  - `.write_iteration(iteration, commit, metric, delta, guard, status, description)`
    — Appends one result row.
  - `.read_recent(n) -> list[dict]` — Returns the last n data rows.

### metric.py

- `MetricResult` — Dataclass: `value: float | None`, `raw_output: str`,
  `success: bool`, `error: str | None`.
- `extract_metric(command, cwd, timeout, metric_pattern) -> MetricResult`
  — Runs a shell command, extracts the last float from stdout (or a custom regex
  group). Non-zero exit is always `success=False`; stdout is capped at 2 000 chars.
- `compare_metrics(baseline, current, direction) -> "improved" | "same" | "worse"`
  — Pure directional comparison; `direction` is `"higher"` or `"lower"`.

### run_manager.py

- `RunConfig` — Dataclass: `goal`, `scope`, `metric_direction`, `verify_command`,
  `guard_command`, `max_iterations`.
- `IterationResult` — Dataclass capturing per-step outcome: `iteration`, `hypothesis`,
  `metric`, `delta`, `guard_passed`, `status`, `checkpoint`, `error`.
- `RunState` — Dataclass carrying mutable run state: `config`, `iteration`,
  `baseline_metric`, `current_best`, `status`, `history`, `journal_path`, `cwd`.
- `RunManager`
  - `.start(config, cwd) -> RunState` — Validates git repo, measures baseline,
    initialises journal; returns `status="failed"` on hard errors.
  - `.step(state, hypothesis, changed_files) -> RunState` — One full iteration:
    `checkpoint_create` → optional guard → `extract_metric` → `compare_metrics`
    → keep or `checkpoint_restore`. Appends to `state.history` and journal.
  - `.should_continue(state) -> bool` — False when `status != "running"` or
    `max_iterations` reached.
  - `.summary(state) -> str` — Human-readable markdown summary of the run.

## Invariants

- **Caller applies changes first.** `RunManager.step()` expects file edits already
  present in the working tree before it is called; it only commits, measures, and
  decides keep/revert.
- **All public functions return structured dicts or dataclasses, never raise.**
  `checkpoint.py` uses `{"error": ...}` returns; `metric.py` uses `MetricResult.success`.
  Callers must check these fields before proceeding.
- **Journal path is fixed to `<cwd>/autoresearch_journal.tsv`** at run start;
  this path is stored in `RunState.journal_path` and must not change during a run.
- **Restore targets `original_head`, not the checkpoint commit.** `checkpoint_create`
  records the pre-commit HEAD; `checkpoint_restore` resets to that SHA, not to any
  branch tip. Any subsequent commits layered on top are also discarded.
- **`_run_autoresearch` avoids circular imports** by importing `RunConfig`,
  `RunManager`, and `_read_input` lazily inside the function body.
- **stdout is capped at 2 000 chars** in `extract_metric` to keep journal/log
  entries compact; this is a hard truncation, not a summary.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial module card authored at HEAD
