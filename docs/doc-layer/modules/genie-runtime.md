---
covers:
  - "genie/runtime/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

`genie/runtime` owns the autoresearch iteration engine: the baseline-measure →
hypothesis-apply → guard-check → metric-compare → keep/revert cycle.
`RunManager` is the single orchestrator; `checkpoint.py` handles git-based
snapshot/restore, `metric.py` extracts float metrics from shell commands,
`journal.py` records every iteration outcome to a TSV file, and
`autoresearch_cli.py` provides the interactive entry-point that wires a live
AI session into the loop.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-runtime.md

- RunManager: sole orchestrator; manages the full iteration lifecycle
- RunConfig: immutable input contract (goal, scope, verify cmd, direction)
- RunState: mutable run snapshot passed between `start` and `step` calls
- checkpoint_create: commits current tree; records original_head for revert
- checkpoint_restore: hard-resets to original_head, discarding the checkpoint commit
- extract_metric: runs a shell command and parses the last float from stdout
- compare_metrics: directional comparison returning improved / same / worse
- JournalWriter: append-only TSV recorder; creates header on first write

## Invariants

- `checkpoint_restore` always resets to `original_head`, not the checkpoint commit — `checkpoint.py:110` — `_run_git(["reset", "--hard", original_head], cwd)`
- Guard failure triggers restore before recording the result — `run_manager.py:181` — `restore_result = checkpoint_restore(checkpoint, state.cwd)`
- Non-zero exit from verify command yields `success=False` regardless of stdout content — `metric.py:51` — `if result.returncode != 0:`
- `extract_metric` caps raw output at 2 000 characters — `metric.py:49` — `raw = result.stdout[:2000]`
- `RunManager.start` returns `status="failed"` immediately if cwd is not a git repo — `run_manager.py:85` — `return RunState(config=config, status="failed", cwd=cwd)`
- `JournalWriter` writes the TSV header only when the file does not yet exist — `journal.py:27` — `if not self.path.exists():`
- Only `"improved"` comparisons advance `current_best`; same or worse reverts — `run_manager.py:245` — `if comparison == "improved":`
- `should_continue` short-circuits on any non-`"running"` status before checking `max_iterations` — `run_manager.py:297` — `if state.status != "running":`
- `_sanitize_label` replaces any character outside `[a-zA-Z0-9_-]` with underscore — `checkpoint.py:35` — `return re.sub(r"[^a-zA-Z0-9_-]", "_", label)`
- Metric extraction uses `metric_pattern` capture group 1 when provided, falls back to last float in stdout — `metric.py:63` — `return MetricResult(`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created
