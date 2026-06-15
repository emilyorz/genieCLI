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

## run .tlv4-v48-decompose-all (run: 2026-06-15)

- `_seed_decompose_and_select` (mcp_trino/research.py) is the single locus for read seed-validation; both standard STANDARD loop call sites (MCP `run_mcp_enhancement` and --direct `_run_optimization_loop`) delegate to it; the function returns a coupled `(winner_sql, winner_measure, events)` 3-tuple so the two fields always come from the same arm — no branch can decouple them.
- trino_optimize.py `baseline()` and `verify()` remain DORMANT (never wired to execute on the read path); the read seed-validation path uses the existing iteration-loop executor (`_produce_decompose_candidate` → `_measure_mcp` / `_measure`) for measurement, not the trino_optimize pipeline.
- `GENIE_V48_SEED_DECOMPOSE` env flag (default `"1"` = ON) gates the read decompose-seed path in all three standard-loop call sites and the no-data advisory path; `tests/conftest.py` autouse fixture sets it to `"0"` for all pre-existing legacy tests, preserving the 1406-test baseline green count.
- `genie/core/llm_adapters.py` holds the shared `_make_advisory_llm_fn` factory (extracted from `write_analysis.py`); `write_analysis.py` re-exports it at line 232 via `from genie.core.llm_adapters import _make_advisory_llm_fn`; both import paths resolve to the same object at runtime.
- plan-cost loops (`_run_mcp_plan_cost_loop` in mcp_trino/research.py and `_run_plan_cost_loop` in trino_query/research.py) do NOT yet use the shared `_seed_decompose_and_select` locus — they contain inline produce→measure→decide blocks (v49 follow-up); this is SAFE because `_plan_cost_loop_core` re-verifies every winning candidate with full `row_equiv_fn` at preflight.py:641 before accepting it as `enhanced_sql`.

## run .tlv4-v49-subquery-decompose (run: 2026-06-15)

- `_should_skip(node)` (`mcp_trino/trino_optimize.py`) ascends `node.parent` chain checking `{Exists, In, CTE, Union, With}` — used symmetrically by both `_extract_fragments` (extract side) and `_apply_rewrites` (apply side) to maintain ordinal symmetry (I3 invariant); adding a new extraction scope must add the corresponding `_should_skip` call on the apply side or ordinals will drift.
- `_pred_ordinal` (extraction) and `_subq_ordinal` (apply) are local monotonic counters in `_extract_fragments` and `_apply_rewrites` respectively; they must share identical TICK-CONDITION logic (same node classes, same `_should_skip` filter, same Subquery-wrapper guard for In) — breaking symmetry silently misroutes rewrites to wrong WHERE positions.
- TICK-CONDITION (§4.3.1): value-list `IN` (no `query` arg, or `query` arg not a `Subquery`, or `q.this is None`) produces NO fragment and NO ordinal tick on both extract and apply sides; only `IN (SELECT ...)` (Subquery-wrapped) ticks and extracts.
- `_is_correlated_exists(inner_select, cte_names)` uses Method A: inner FROM/JOIN tables collected via `inner_select.args.get("from_")` (underscore key — `"from"` returns None); `col.table` is a string property (not an `Identifier` object); CTE-alias check: `inner_tables & {n.lower() for n in cte_names}`; fail-conservative (returns True on any exception).
- `_assign_subq_ordinals` is a retained API stub (no-op); ordinals for role=subquery fragments are assigned inline in `_extract_fragments` via `_pred_ordinal`; the post-pass function is a no-op for API stability (spec §3.4).
- `_skip_subq_pass` guard in `_apply_rewrites`: when both `__root__` and any `__subquery_N__` key are present in `active_rewrites`, the entire subquery-ordinal branch is skipped (I5 / TR-COLL) — the tree was already re-based on the root rewrite so original ordinals are stale.
- Subquery kinds shipped in v49: WHERE EXISTS, WHERE IN (subquery). Out of scope and not extracted: derived tables (FROM subquery), UNION arms, scalar subqueries (SELECT-list), correlated subqueries are extracted but not cost-read (`is_independently_runnable=False`).
- `_apply_rewrites` recompose for Exists: `node.set("this", repl)` where `node` is the `Exists` AST node and `repl` is a bare `Select` (unwrap any `Subquery` wrapper first); for In: `q.set("this", repl)` where `q = node.args.get("query")` is the `Subquery` wrapper.
- The `continue  # NEUTERED` anti-pattern (early continue that kills an entire if-branch body without executing the intended work) was the root cause of the v49 deliverable gap; grep for bare `continue` lines inside `if node_class == "<type>":` branches when auditing future extract/apply loops.
