"""
runtime — Control plane for the autoresearch iteration loop.

Modules:
  checkpoint  — Git-based snapshot/restore operations
  journal     — TSV iteration log compatible with autoresearch schema
  metric      — Command-driven metric extraction and comparison
  run_manager — Orchestration: baseline → step → keep/revert cycle
"""
