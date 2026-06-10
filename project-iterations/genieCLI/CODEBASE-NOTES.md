# CODEBASE-NOTES — durable facts for tlv4 runs (verify-then-trust)

> Cross-run knowledge file (tlv4 driver `NOTES` config). Read FIRST when exploring;
> VERIFY file:line claims against HEAD before citing — entries may predate recent commits.
> Append-only: wrap_retro adds a `## run <ledger-dir> (run: <date>)` section per run.
> DURABLE facts only (true at any commit): module map, conventions, invariants, traps.
> Never run status, scores, or task outcomes.

## seed (curated from v42-v45 evidence, 2026-06-10, HEAD 966f9b3)

### Architecture / module map
- /trino-research has TWO sibling entry paths: `genie/skills/mcp_trino/research.py` (MCP, production default) and `genie/skills/trino_query/research.py` (--direct, opt-in); ANY cross-cutting change must be wired into BOTH or it silently no-ops on one (historical drift incidents: v28 no-data, v44 direct directions block).
- Shared pure cores live in `genie/skills/mcp_trino/preflight.py`: `_plan_cost_loop_core` (plan-cost iteration, v43), `build_preflight_decision` + `PreflightRoute` + `PreflightDecision` (dispatch state machine, v45) — the proven pattern is "decision shared+pure, execution path-specific via injected callables"; extend these, do not re-grow parallel gate logic in the two research.py files.
- `pre_execution_diagnosis.py` assembles ranked OptimizationDirection from six pure contributors (static R1-R10 / sql-shape / explain-cost / join-diagnosis / metadata / runtime-memory); ranking = severity then `_SOURCE_RANK` keyed by the EVIDENCE PREFIX (e.g. "static:", "explain:") — a new contributor must reuse a registered prefix or add one, or it silently ranks last (rank 9 default).
- `format_directions_for_prompt` caps at 6 directions (+K-more signal on truncation); the markdown report path lists all.
- Static rules R1-R10 live under `genie/skills/trino_query/sql_static/`; `rule_gate.py` maps each to BLOCK/REWRITE/ADVISE; `tests/test_rule_id_contract.py` enforces exact registration coverage — adding rule R11+ requires all five registration points or that contract test fails (by design, after the v32 silent fall-through).
- Write/CTAS path: `write_analysis.py` (v35 safety contract: SQL executed=no / EXPLAIN=no on write path) + `_run_decompose_advisory` with column gate (v40) + semantic gate `queries_structurally_equivalent` in `genie/core/sql_extraction.py` (v42, fail-closed tri-state).
- Metadata contributor input: after v46, --direct fetches real metadata via `_fetch_table_metadata_direct` (in `trino_query/research.py`); metadata-unavailable note is now conditional on fetch outcome. The prior seed fact "(MCP-only)" is superseded.

### Conventions / invariants
- Tests: pytest, `test_should_<behavior>_when_<scenario>` naming; realistic EXPLAIN plan fixtures live in `tests/fixtures/explain_plans/`; acceptance-test files from tlv4 runs are named `test_<feature>_acceptance.py`.
- Refactors require characterization tests FIRST (lock current routes pre-change, rerun post-change); behavior divergences must be disclosed + test-pinned, never hidden (v45 D5 precedent).
- Fail-open discipline for diagnosis contributors: malformed input → return [] / today's behavior; NEVER fabricate a signal on missing estimates (join-stats-gap pattern).
- No-data classification errs toward None (surface the real error) — "does not exist" free-text alone must never classify; structured errorName first (v45 S2).
- Comparison soundness in sql_extraction: ORDER BY is positional (sequence-sensitive under LIMIT), JOIN ON/USING and FROM are compared per-join; frozenset (commutative) comparisons are only sound for genuinely commutative clauses — the v42 false-admit classes (FROM/ORDER BY/ON/USING) are pinned by tests.
- Thresholds in diagnosis are named constants with written rationale (e.g. LARGE_SCAN_BYTES, MEMORY_PRESSURE_FRACTION env-overridable) — no magic numbers.

## run .tlv4-v46-direct-metadata (run: 2026-06-10)

- `_fetch_table_metadata_from_runner(tables, execute_fn, …)` in `mcp_trino/research.py` is the shared pure helper for table metadata probing; `execute_fn: (str) -> list[dict]` is injected — pass `_make_mcp_execute_fn(client)` for MCP, `_execute_direct_as_dicts` for --direct. Both paths share identical Probe 1 (columns) + Probe 2 (properties) logic.
- `_execute_via_mcp(client, sql)` returns an envelope dict `{rows, columns, error, …}`, NOT `list[dict]` — any adapter that passes the raw return to an iterator over rows will silently iterate dict keys. Always extract `result.get("rows") or []`; `_make_mcp_execute_fn` is the canonical adapter pattern (v46).
- `_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")` (module-level constant in `mcp_trino/research.py`) — stricter than `^[A-Za-z0-9_]+$`; rejects identifiers starting with digits; silently skips quoted/hyphenated Trino identifiers (fail-open, validated by mocks only).
- `_assemble_direct_directions` returns a 2-tuple `(directions: list, pre_table_metadata: list)` after v46; all call sites use `directions, _ = …` (4 sites in `trino_query/research.py`). Previously returned a bare list.
- `_extract_table_names` (private underscore) is the name used at all call sites including cross-module imports; no public alias `extract_table_names` exists — importing the public form raises ImportError.
