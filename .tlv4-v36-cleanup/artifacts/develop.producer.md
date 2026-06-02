# Develop v36 Attempt 1

## Summary

Implemented the repo-local v36 cleanup ticket without live cluster probes and without external Task Ledger tooling edits.

Changed files:

- `genie/skills/mcp_trino/preflight.py`
- `genie/skills/mcp_trino/research.py`
- `tests/test_per_node_memory_limit.py`

## Changes

1. Updated `make_query_max_run_time_sql()` docstring to match current behavior:
   - timeout derives through `make_candidate_timeout_ms()`;
   - `CANDIDATE_TIMEOUT_HEADROOM` is currently 2x;
   - floor is 2000ms.

2. Improved `bad_env_fallthrough` progress output in `run_mcp_enhancement()`:
   - bad env + SHOW SESSION value: message says SHOW SESSION supplied `query_max_memory_per_node=<N> GiB`;
   - bad env + no usable SHOW SESSION value: message says `1.0 GiB fallback` is active.

3. Repaired T-F-24 coverage:
   - the test now supplies EXPLAIN JSON with estimates and a long baseline so plan-cost mode is entered;
   - `_run_mcp_plan_cost_loop` must be called;
   - `peak_memory_limit_bytes == 5 * 1024**3` is asserted unconditionally.

4. Added bad env + failed SHOW SESSION breadcrumb coverage.

## Verification

Passed:

- `python3 -m pytest tests/test_mcp_preflight.py::TestMakeQueryMaxRunTimeSql -q` -> `4 passed`
- `python3 -m pytest tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run -q` -> `1 passed, 1 warning`
- `python3 -m pytest tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_bad_env_show_session_failure_breadcrumb_uses_fallback -q` -> `1 passed, 1 warning`
- `python3 -m pytest tests/test_per_node_memory_limit.py -q` -> `31 passed, 1 warning`
- `python3 -m pytest tests/test_mcp_plan_cost_loop.py -q` -> `12 passed, 1 warning`
- `python3 -m pytest tests/test_mcp_preflight.py -q` -> `46 passed`
- `python3 -m pytest -q` -> `935 passed, 1 warning`
- `git diff --check` -> clean

The warning is the existing macOS Python / urllib3 LibreSSL warning.

## Residuals

- Live-cluster numeric validation remains excluded by Sam's instruction.
- `validate_ledger.py` v3 schema support remains out-of-repo / external-tooling residual.
- v35 optional residuals remain outside v36 scope.

## Commit

No commit made. Per V4, commit is deferred until Review passes and landing is requested or chosen by controller policy.
