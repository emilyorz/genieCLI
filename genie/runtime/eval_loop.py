"""EvalLoop — high-level autoresearch orchestration re-export.

The actual orchestration is in run_manager.py; this module provides
the public API surface for cli.py to import.
"""
from genie.runtime.run_manager import IterationResult, RunConfig, RunManager, RunState

__all__ = ["RunConfig", "RunManager", "RunState", "IterationResult"]
