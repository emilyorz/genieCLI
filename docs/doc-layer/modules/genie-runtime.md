---
covers:
  - "genie/runtime/*.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

`genie/runtime` owns the autoresearch iteration engine: the baseline → hypothesis → verify → compare → keep/revert cycle that drives autonomous code improvement. It provides git-based checkpointing (`checkpoint.py`), shell-command metric extraction and comparison (`metric.py`), TSV iteration journaling (`journal.py`), top-level loop orchestration (`run_manager.py`), and the interactive CLI entry point that wires an LLM session into the loop (`autoresearch_cli.py`).

## Exports

from __future__ import annotations
import: subprocess
from pathlib import Path
from typing import Callable
from genie.core.context import SkillContext
from genie.core.registry import SkillRegistry
from genie.core.tool_call import normalize_result, parse_tool_call
from genie.session.manager import new_msg, new_session
function: def _is_tool_failure(result) -> bool (autoresearch_cli.py:20)
function: def _run_autoresearch(provider, cfg, model, reasoning, output, build_prompt) -> None (autoresearch_cli.py:33)
from __future__ import annotations
import: re
import: subprocess
import: time
from pathlib import Path
function: def _run_git(args, cwd) -> tuple[int, str, str] (checkpoint.py:22)
function: def _sanitize_label(label) -> str (checkpoint.py:33)
function: def git_is_repo(cwd) -> bool (checkpoint.py:42)
function: def git_is_clean(cwd) -> bool (checkpoint.py:48)
function: def checkpoint_create(label, cwd) -> dict (checkpoint.py:54)
function: def checkpoint_restore(checkpoint_info, cwd) -> dict (checkpoint.py:96)
from __future__ import annotations
import: csv
from pathlib import Path
class: JournalWriter (journal.py:19)
  method: def __init__(self, path) -> None (journal.py:25)
  method: def _write_header(self) -> None (journal.py:34)
  method: def _append_row(self, row) -> None (journal.py:38)
  method: def write_baseline(self, metric, commit, description) -> None (journal.py:46)
  method: def write_iteration(self, iteration, commit, metric, delta, guard, status, description) -> None (journal.py:63)
  method: def read_recent(self, n) -> list[dict] (journal.py:84)
from __future__ import annotations
import: re
import: subprocess
from dataclasses import dataclass
from typing import Literal
class: MetricResult (metric.py:19)
function: def extract_metric(command, cwd, timeout, metric_pattern) -> MetricResult (metric.py:26)
function: def compare_metrics(baseline, current, direction) -> Literal['improved', 'same', 'worse'] (metric.py:95)
from __future__ import annotations
import: shlex
import: subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from genie.runtime.checkpoint import checkpoint_create, checkpoint_restore, git_is_repo
from genie.runtime.journal import JournalWriter
from genie.runtime.metric import compare_metrics, extract_metric
class: RunConfig (run_manager.py:29)
class: IterationResult (run_manager.py:39)
class: RunState (run_manager.py:51)
class: RunManager (run_manager.py:66)
  method: def start(self, config, cwd) -> RunState (run_manager.py:77)
  method: def step(self, state, hypothesis, changed_files) -> RunState (run_manager.py:122)
  method: def should_continue(self, state) -> bool (run_manager.py:295)
  method: def summary(self, state) -> str (run_manager.py:303)

## Invariants

- `RunManager` is the only stateful class; `RunConfig`, `IterationResult`, and `RunState` are plain dataclasses with no behaviour. (run_manager.py:8–9, run_manager.py:29–59)
- `start()` requires a git repository; returns `status="failed"` immediately if `git_is_repo()` returns False — it never raises. (run_manager.py:84–85)
- `step()` expects the caller to have already applied file changes before the call; `step()` commits them via `checkpoint_create`, not the caller. (run_manager.py:128–141)
- Non-improved iterations are reverted via `git reset --hard original_head`; if the restore itself fails, `RunState.status` is set to `"failed"` and the loop must stop. (run_manager.py:248–272, checkpoint.py:110–113)
- `checkpoint_create` records `original_head` (the SHA before staging) so `checkpoint_restore` can return to the exact pre-step state. (checkpoint.py:68–70, checkpoint.py:96–114)
- All `checkpoint_*` and `_run_git` functions return plain dicts (`{"ok": ...}` or `{"error": "..."}`) — they never raise. (checkpoint.py:6–8)
- `extract_metric` runs the verify command with `shell=True`; non-zero exit codes always yield `success=False` regardless of stdout content. (metric.py:51–57)
- Raw stdout captured by `extract_metric` is capped at 2 000 characters to limit downstream log size. (metric.py:49)
- Without a `metric_pattern`, `extract_metric` returns the **last** float found in stdout via `re.findall`. (metric.py:75–83)
- `JournalWriter` writes a TSV header only if the file does not exist; subsequent opens always append. (journal.py:26–28, journal.py:39–40)
- TSV column schema is fixed: `iteration | commit | metric | delta | guard | status | description`. (journal.py:16)
- `guard_command` (optional) runs with a 120 s timeout; any exception counts as failure and triggers revert. (run_manager.py:168–178)
- `_is_tool_failure` in `autoresearch_cli.py` matches only on known prefix strings — unrecognised error shapes from tools silently pass through as successes. (autoresearch_cli.py:20–30)

## Change log

572f7ff30399bed1a1a3c230918ba037ae874272: initial card
