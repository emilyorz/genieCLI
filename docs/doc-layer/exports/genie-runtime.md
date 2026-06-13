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
