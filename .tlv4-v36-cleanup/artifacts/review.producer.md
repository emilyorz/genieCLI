# Review Artifact - genieCLI v36

## Scope Compliance

PASS. Actual diff is limited to repo-local v36 cleanup scope:

- `genie/skills/mcp_trino/preflight.py`
- `genie/skills/mcp_trino/research.py`
- `tests/test_per_node_memory_limit.py`

No external Task Ledger tooling was edited. `validate_ledger.py` v3 schema support remains an external residual, as required.

No live-cluster validation was performed or claimed. This means no `mcp-trino`, `localhost:8811`, live `SHOW SESSION`, production casing/format, or numeric 0.5 fraction validation.

## Diff Summary

- `preflight.py`: updates `make_query_max_run_time_sql()` docstring to match current `make_candidate_timeout_ms()` behavior: 2x headroom and 2000ms floor.
- `research.py`: improves `bad_env_fallthrough` breadcrumb so failed/unusable `SHOW SESSION` explicitly says the 1.0 GiB fallback is active.
- `tests/test_per_node_memory_limit.py`: makes T-F-24 executable by forcing plan-cost path entry, asserting `_run_mcp_plan_cost_loop` is reached, and checking `peak_memory_limit_bytes == 5 * 1024**3` unconditionally. Adds coverage for bad env + failed `SHOW SESSION` breadcrumb.

## Test Evidence

Develop artifact reports:

- `tests/test_mcp_preflight.py::TestMakeQueryMaxRunTimeSql` -> `4 passed`
- T-F-24 targeted test -> `1 passed, 1 warning`
- new breadcrumb targeted test -> `1 passed, 1 warning`
- `tests/test_per_node_memory_limit.py` -> `31 passed, 1 warning`
- `tests/test_mcp_plan_cost_loop.py` -> `12 passed, 1 warning`
- `tests/test_mcp_preflight.py` -> `46 passed`
- full suite `python3 -m pytest -q` -> `935 passed, 1 warning`
- `git diff --check` -> clean

Controller-side recheck after develop review:

- `python3 -m pytest tests/test_mcp_preflight.py::TestMakeQueryMaxRunTimeSql tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_bad_env_show_session_failure_breadcrumb_uses_fallback -q` -> `6 passed, 1 warning`
- `git diff --check` -> clean

Warning is the existing macOS Python / urllib3 LibreSSL warning.

## Remaining Risks / Residuals

- No live-cluster validation. This is intentional, but real Trino `SHOW SESSION` casing/format and production numeric behavior remain unverified.
- External `validate_ledger.py` v3 schema support remains unresolved and out of repo scope.
- v35 optional residuals remain outside v36 scope.

## Recommendation

PASS.
