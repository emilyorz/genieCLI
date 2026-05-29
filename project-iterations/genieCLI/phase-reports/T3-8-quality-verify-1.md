# Quality Verification Report - T3

**Verdict (re-review, current)**: APPROVED — >9.0 gate CLEARED
**Quality score (re-review)**: 9.3/10
**Verdict (round 1, superseded)**: CHANGES_REQUIRED — 8.9/10
**Context Packet**: T3-8 (zero-cost directed report, both paths)

> Round-1 caveats now resolved: this re-review HAD a shell and independently re-ran the
> suite (**781 passed**, see Re-review). The two Important items from round 1 are fixed and
> re-verified. The round-1 body below is retained verbatim for audit; the **Re-review**
> section at the end is authoritative.

---

## Round 1 (8.9/10, CHANGES_REQUIRED) — retained for audit

> Gating note: this quality review ran while the T3 **spec-verifier verdict is still
> PENDING** — `CURRENT.md` Step 8 records both verifiers as "dispatch pending" and no
> `T3-8-spec-verify-*.md` exists on disk. Per the quality-verifier input contract I run
> only after spec-verifier returns `SPEC_COMPLIANT`. Treat this report as advisory until
> the spec verdict lands; the controller must not merge T3 on this report alone.
>
> Verification honesty: I have **no Bash/shell tool** in this dispatch, so I could NOT
> re-run the 780-test suite myself. The "780 passed" figure is the executor's Dev claim
> (CURRENT.md Step 7), not independently reproduced here. All findings below are from
> reading the diff, the tests, and the surrounding code.

### Strengths

- `mcp_trino/research.py:628` `_assemble_mcp_directions` — single source of truth for all
  three MCP call sites (prompt injection, gate-trip, `--diagnose-only`). Genuine DRY, not
  copy-paste; returns `(directions, table_metadata)` so the success path reuses the one
  metadata fetch.
- `trino_query/research.py:646` `_assemble_direct_directions` is a faithful mirror of the
  MCP assembler — same `plan_cost` import, same never-raise contract, differing only in
  `table_metadata=None` (correct: the direct path has no metadata fetcher). By code reading
  the symmetry is real; the drift risk is the _absence of a test_, not present divergence.
- `pre_execution_diagnosis.py:402` pipe-escape `rationale.replace("|","\\|")` is applied,
  and the empty-directions branch (`:387`) returns before emitting a table header.
- `preflight.py:234` `LongQueryAbort.report_markdown` added as an optional kwarg with a
  default — backward compatible; T2 raise sites that omit it still work.
- `_direct_explain_runner` (`trino_query/research.py:1268`) narrower row extraction than the
  MCP runner is CORRECT, not a regression: `_execute_sql` (`:42`) returns DBAPI
  `cur.fetchall()` tuples, never dict rows. Error→None mirrors the MCP runner and matches
  `plan_cost`'s best-effort contract.
- Status/exception asymmetry (MCP raises+caught, `--direct` returns dict) is principled —
  mirrors each path's pre-existing abort mechanics, confirmed in T1 Explore findings.

### Issues (round 1)

- [Important] every path-level test mocked BOTH assemblers → dual-path **symmetry had zero
  test coverage**; the "symmetry" test only compared report headers. Tests were NOT
  tautological (zero-query-cost proven via real `assert_not_called`), but the
  assembler-equivalence guard was missing.
- [Important] `trino_query/research.py:1302` orphaned `status == "aborted"` branch rendered
  unreachable by T3.
- [Nit] `_direct_explain_runner` has no direct unit test (parallel to T2's parked sibling).
- [Nit] repeated local `from datetime import datetime`.
- [Nit] `format_directions_report` hardcoded "(table is pre-sorted)" parenthetical.

---

## Re-review (AUTHORITATIVE) — 9.3/10, APPROVED

**Suite re-run independently this round:**
`cd /Users/leeabc/work/emilyorz/genieCLI && .venv/bin/python -m pytest -q` →
**`781 passed in 1.66s`**, zero failures (780 → 781: +1 equivalence test). Reproduced by me,
not taken on claim.

### Fix 1 — orphaned `"aborted"` branch DELETED — VERIFIED

- `grep -rn aborted genie/skills/trino_query/research.py` → no matches.
- `grep '"status".*"aborted"' genie/` (whole package) → **zero producers anywhere**, so
  deleting the handler creates no silent fall-through regression: no live path emits
  `"aborted"` that would now drop to the summary block.
- Entry-point status flow (`research.py:1299-1329`) is now clean: `failed` / `diagnosed` /
  `no_data` / fallthrough-to-summary. Correct and exhaustive for the statuses the loop
  actually returns.

### Fix 2 — dual-path symmetry now has a REAL unmocked guard — VERIFIED

- `tests/test_zero_cost_directed_report.py:311` `test_both_assemblers_produce_identical_directions_for_same_inputs`
  drives BOTH `_assemble_mcp_directions` and `_assemble_direct_directions` UNMOCKED with the
  same `static_report` + same peak, asserts `mcp_metadata == []` (MagicMock client → no
  resolvable refs → metadata contributor empty, reducing MCP to static+explain), then
  asserts the non-explain-sourced direction tuples `(kind, severity, target_metric)` are
  **equal** across both paths plus shared `{fix-cartesian-join, memory-pressure}` kinds on
  both. This is exactly the equivalence guard I specified and it directly defends the v28
  silent-drift lesson (peak-memory extraction changing on one path only).
- Honest scope of the guard (the test documents it at `:341-347`): explain-sourced
  directions are excluded from the equality because the MagicMock MCP client cannot return a
  real plan while the direct side gets `_PLAN_JSON` — so the two paths' explain output can't
  be compared apples-to-apples in one test. Acceptable: the explain axis is independently
  covered by `pre_execution_diagnosis`'s own T1 tests (both assemblers funnel through it),
  and the static + runtime axis — where drift would actually bite — is now guarded exactly.
  Symmetry is now VERIFIED for the contributors that matter, not merely implemented.

### Fix 3 — Nit reworded — VERIFIED

- `pre_execution_diagnosis.py:411` now reads
  "(the table above is ranked by severity)" — disambiguates the ranked-directions table from
  a SQL table. Correct.

### Parked (accepted)

- `_direct_explain_runner` / `_build_mcp_explain_runner` lack direct unit tests — both
  trivial, None-safe, exercised indirectly. Parked to RETRO together. Reasonable.
- Repeated local `from datetime import datetime` — cosmetic, left as-is. Fine.

### Score rationale

9.3/10. Both Important items resolved and independently re-verified; suite green at 781
with zero regression; the symmetry guard is genuine and well-scoped (not a fig-leaf).
Docked 0.7: the equivalence test cannot compare the explain-sourced axis across paths
(inherent to mocking the MCP client), and two helper closures remain without direct tests —
minor residual coverage gaps, none blocking. Clears the >9.0 strict gate.

### Standing caveat (unchanged, controller's call)

The T3 **spec-verifier verdict** was PENDING when round 1 ran. If spec-verifier has since
returned `SPEC_COMPLIANT`, this APPROVED quality verdict stands and T3 may proceed. If the
spec verdict is still outstanding, the controller must still gate the merge on it — quality
APPROVED does not substitute for spec compliance.

### Top items (none blocking)

1. Confirm the T3 spec-verifier verdict is `SPEC_COMPLIANT` before merge (controller gate).
2. RETRO: park the two explain-runner unit-test gaps together with the T2 sibling.
