# Wrap / Retro - genieCLI v36 Cleanup

## Outcome summary

v36 completed the repo-local cleanup scope from the v34 residual list without
live-cluster probes and without external Task Ledger tooling edits.

Closed in the working tree:

- `make_query_max_run_time_sql()` docstring now matches the current
  `make_candidate_timeout_ms()` behavior: 2x headroom and 2000ms floor.
- T-F-24 is now executable: the test forces MCP plan-cost path entry and
  unconditionally asserts `_run_mcp_plan_cost_loop(...,
peak_memory_limit_bytes=5 * 1024**3)`.
- `bad_env_fallthrough` breadcrumb now distinguishes usable `SHOW SESSION`
  from failed/unusable `SHOW SESSION`, and explicitly says the `1.0 GiB`
  fallback is active.

Current repo state is uncommitted:

- Modified: `genie/skills/mcp_trino/preflight.py`
- Modified: `genie/skills/mcp_trino/research.py`
- Modified: `tests/test_per_node_memory_limit.py`
- Untracked TLV4 artifacts: `.tlv4-v36-cleanup/`

No commit was made. No push was made.

## Verification summary

Artifact gate status:

- Ticket review: `score=9.4`, `pass_bool=true`, `hard_fails=[]`
- Develop review: `score=9.3`, `pass_bool=true`, `hard_fails=[]`
- Review review: `score=9.3`, `pass_bool=true`, `hard_fails=[]`

Develop artifact reports these checks passed:

- `python3 -m pytest tests/test_mcp_preflight.py::TestMakeQueryMaxRunTimeSql -q`
  -> 4 passed
- `python3 -m pytest tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run -q`
  -> 1 passed, 1 warning
- `python3 -m pytest tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_bad_env_show_session_failure_breadcrumb_uses_fallback -q`
  -> 1 passed, 1 warning
- `python3 -m pytest tests/test_per_node_memory_limit.py -q`
  -> 31 passed, 1 warning
- `python3 -m pytest tests/test_mcp_plan_cost_loop.py -q`
  -> 12 passed, 1 warning
- `python3 -m pytest tests/test_mcp_preflight.py -q`
  -> 46 passed
- `python3 -m pytest -q`
  -> 935 passed, 1 warning
- `git diff --check`
  -> clean

Review artifact reports controller-side recheck:

- Focused combined pytest -> 6 passed, 1 warning
- `git diff --check` -> clean

Wrap producer read-only checks:

- `git diff --stat` shows only the three expected repo-local product/test files.
- `git diff --check` produced no output.
- No live `mcp-trino`, `localhost:8811`, or live `SHOW SESSION` probe was
  performed or claimed.

The warning is the existing macOS Python / urllib3 LibreSSL warning.

## Worked

- Scope stayed tight. v36 touched only the repo-local cleanup files named by the
  ticket.
- The T-F-24 fix is materially stronger than the old test: it now fails if the
  MCP plan-cost loop is not entered or if the resolved 5 GiB limit is not
  threaded.
- Breadcrumb copy now reflects the real effective behavior: bad env can fall
  through to `SHOW SESSION`, but if that fails the user sees that the 1.0 GiB
  fallback is active.
- Review gate integrity held: reviewed steps were hash-bound, scored above 9,
  and had no hard fails.
- External Task Ledger tooling was not edited under a repo-scoped v36 run.

## Failed

- Live-cluster numeric validation remains unclosed. This is not a v36
  implementation failure; Sam explicitly excluded office `mcp-trino`,
  `localhost:8811`, live `SHOW SESSION`, real casing/format, and production
  fraction validation from this run.
- `validate_ledger.py` v3 schema support remains unresolved because it is
  external Task Ledger tooling, not genieCLI repo product code.
- Repo status/docs still need to be updated so `STATUS.md` no longer lists the
  now-fixed v34 minor residuals as backlog.
- Ticket review correctly noted that P3 residual recording had weak acceptance
  criteria; wrap/status documentation is the place to close that process gap.
- No commit or push occurred.

## Change next

- Update `project-iterations/genieCLI/STATUS.md` to add v36 as a TLV4
  bugfix-profile cleanup and mark these v34 minors closed: stale timeout
  docstring, T-F-24 conditional assertion, and `bad_env_fallthrough` fallback
  breadcrumb.
- Keep live-cluster numeric validation visible as an unresolved residual, with
  explicit note that Sam excluded the office live probe from v36.
- Move `validate_ledger.py` v3 schema support out of genieCLI repo carryover
  wording and into an out-of-repo Task Ledger tooling residual.
- Add a v36 touchpoint to `project-iterations/genieCLI/features/trino-research.md`
  for the user-observable breadcrumb: bad env plus failed `SHOW SESSION` now
  says the 1.0 GiB fallback is active.
- Add a v36 archive/status note for `.tlv4-v36-cleanup` before landing, without
  implying the untracked TLV4 artifacts have already been committed.

## Residual classification

- promote P1 M - Live-cluster numeric validation remains residual. Sam
  explicitly excluded office `mcp-trino`, `localhost:8811`, live
  `SHOW SESSION`, real casing/format, and production numeric fraction
  validation from v36, so this cannot be closed by mock/unit evidence. Next
  action requires a separate Sam-authorized live-env validation run.
- promote P1 S - `validate_ledger.py` v3 schema support remains residual, but
  it is out-of-repo. Target Task Ledger tooling templates, not genieCLI product
  code.
- drop - v34 minor residual: stale `make_query_max_run_time_sql()` docstring.
  Closed by v36 diff.
- drop - v34 minor residual: T-F-24 conditional assertion never fires. Closed by
  v36 test fixture and unconditional assertion.
- drop - v34 minor residual: `bad_env_fallthrough` breadcrumb omits final
  fallback. Closed by v36 breadcrumb copy and unit coverage.
- park age 1/3 - v35 optional residuals remain outside v36 scope: report
  timestamp collision, default MCP interactive paste reachability-before-paste
  limitation, and preferred test-file shape mismatch. Trigger: re-promote only
  if the same issue blocks a user-visible run or a future TLV4 ticket names it
  as scope.

## Repo docs/status update suggestion

Recommended status wording:

```markdown
- **v36 (TLV4 bugfix profile, uncommitted):** repo-local cleanup of v34
  memory-limit residuals. Fixed stale `make_query_max_run_time_sql()`
  docstring, made T-F-24 executable for MCP plan-cost memory-limit threading,
  and clarified bad env + failed `SHOW SESSION` breadcrumb to state the 1.0 GiB
  fallback is active. No live office `mcp-trino` / `localhost:8811` /
  `SHOW SESSION` numeric validation was performed; that remains a separate
  residual. External `validate_ledger.py` v3 schema support remains out-of-repo.
```

Recommended commit message, after docs/status are updated and landing is
allowed:

```text
fix(trino-research): close v36 memory cleanup residuals

- update query timeout docstring for 2x/2000ms behavior
- make MCP memory-limit threading coverage executable
- clarify bad-env SHOW SESSION fallback breadcrumb
- record live-cluster and validator residuals

Task-Ledger-Run: v36-cleanup
Task-Ledger-Step: develop
Task-Ledger-Attempt: 1
```
