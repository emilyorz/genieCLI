# v59 Ticket — real-environment validation + status repair for v57/v58 `/trino-research`

## Problem / motivation

The current repo `HEAD`/`main`/`origin/main` is `6cd9640`, and v57/v58 have shipped important `/trino-research` safety and parity fixes:

- **v57** (`e11c472`, closed by `1581e44`): bounded model-call budget.
  - Default decompose seed is evidence-only.
  - Fragment-level LLM rewrite is opt-in via `GENIE_FRAGMENT_REWRITE=1`.
  - Fragment rewrite is bounded by `GENIE_FRAGMENT_REWRITE_CAP`.
  - Provider/model failures are preserved as reportable iterations instead of disappearing.
- **v58** (`6cd9640` after commit-sync): `--direct` parity for the v57 fragment-rewrite opt-in behavior across no-data, plan-cost, and standard-loop paths.

However, `project-iterations/genieCLI/STATUS.md` is stale: it still presents the active/latest state around v48/v46 and does not reflect v57/v58 or the current v59 task. This makes the project dashboard misleading for future Task Ledger runs and Sam/Emily handoff.

Sam asked Emily to start **v59 using Task Ledger V4**. Hermes Emily’s intended v59 scope is narrow: **real-environment validation + status repair**.

The gap to close is not a new optimizer strategy. It is confidence and documentation:

1. Repair status/iteration docs so they describe the real current state through v58 and the v59 validation task.
2. Add or prepare a repeatable validation path for v57/v58 behavior in a representative local environment, with an optional live-company-environment mode if the required Trino/Qwen environment is actually available.
3. Be explicit and honest if company live validation cannot be run.

## Expected outcome

Develop should produce a small docs/validation patch that makes the repo self-explanatory at `HEAD`:

- `STATUS.md` no longer claims v48/v46-era status as current.
- v57/v58 shipped behavior is summarized accurately with links/pointers to the TLV4 wrap artifacts.
- v59 is recorded as the current narrow validation/status-repair iteration.
- A local representative validation script and/or checklist exists for v57/v58 behavior.
- If a real company Trino/Qwen environment is available, the validation artifact records the live result.
- If that environment is unavailable, the validation artifact clearly says live validation is **pending**, and records only local representative results as completed.
- No new rewrite strategy is introduced.

## Fix scope

Keep the implementation narrow. Likely affected files:

- `project-iterations/genieCLI/STATUS.md`
  - Repair “Active Iteration” / latest-status language.
  - Bring the dashboard forward from stale v48/v46 wording to current `HEAD` through v58.
  - Add v59 as active/current scope: real-environment validation + status repair.
  - Mention v57/v58 behavior and evidence sources:
    - `.tlv4-v57-model-call-budget/artifacts/wrap_retro.producer.md`
    - `.tlv4-v58-direct-fragment-optin/artifacts/wrap_retro.producer.md`
  - Update touched feature reference for `/trino-research` as needed.

- `project-iterations/genieCLI/archive/v57.md` and/or `project-iterations/genieCLI/archive/v58.md`
  - Add concise archive records if Develop decides STATUS should not carry all detail inline.
  - These should be factual summaries only, sourced from the wrap retros and commits.

- `project-iterations/genieCLI/archive/v59.md` or `project-iterations/genieCLI/phase-reports/v59-*.md`
  - Optional, but useful if the validation/status-repair run needs a durable record outside the TLV4 artifact directory.
  - Should distinguish local representative validation from live company validation.

- `scripts/validate_trino_research_v57_v58.py` or similar
  - Add a repo-local validation helper if no suitable script exists.
  - The script should be safe to run without company credentials.
  - Suggested behavior:
    - Default/local mode: run representative checks for v57/v58 behavior using existing tests or mocked/local harnesses.
    - Optional live mode: only run if explicit env/config flags are present.
    - Markdown output: write a validation report under `report/` or print a clear checklist/result to stdout.
    - If live env is unavailable, record `LIVE VALIDATION: PENDING` with a reason, not a fake pass.

- `tests/...` only if needed for validation-script support.
  - Likely existing tests already cover most behavior:
    - `tests/test_decompose_then_iterate.py`
    - `tests/test_mcp_research.py`
    - possibly `tests/test_step_trace.py`
    - possibly `tests/test_trino_optimize.py`
  - Avoid broad refactors.

- Existing production files should normally **not** need changes:
  - `genie/skills/mcp_trino/research.py`
  - `genie/skills/trino_query/research.py`
  - `genie/skills/mcp_trino/trino_optimize.py`
  - `genie/skills/mcp_trino/preflight.py`

If Develop believes production code must change, stop and document why in the develop artifact before changing it. This v59 ticket is intended as validation/docs repair, not behavior work.

## Validation plan and commands

Minimum local validation:

```bash
python -m py_compile scripts/validate_trino_research_v57_v58.py
```

If a validation script is added, run its offline/local mode:

```bash
python scripts/validate_trino_research_v57_v58.py --offline
```

Targeted regression for v57/v58 areas:

```bash
python -m pytest tests/test_decompose_then_iterate.py tests/test_mcp_research.py -q
```

Recommended broader targeted sweep, matching v57-relevant surfaces:

```bash
python -m pytest \
  tests/test_mcp_research.py \
  tests/test_strategy_verify.py \
  tests/test_decompose_then_iterate.py \
  tests/test_step_trace.py \
  tests/test_trino_optimize.py \
  tests/test_critical_path.py \
  -q
```

Full suite if runtime permits:

```bash
python -m pytest -q
```

Documentation sanity checks:

```bash
grep -n "v59\|v58\|v57" project-iterations/genieCLI/STATUS.md
grep -n "GENIE_FRAGMENT_REWRITE\|GENIE_FRAGMENT_REWRITE_CAP" project-iterations/genieCLI/STATUS.md
```

Optional live validation, only if the real company Trino/Qwen environment is available and explicitly configured:

```bash
GENIE_FRAGMENT_REWRITE=0 \
python scripts/validate_trino_research_v57_v58.py --live
```

```bash
GENIE_FRAGMENT_REWRITE=1 \
GENIE_FRAGMENT_REWRITE_CAP=1 \
python scripts/validate_trino_research_v57_v58.py --live
```

The live command names/flags may be adjusted by Develop, but the validation output must clearly state:

- environment used,
- whether Qwen/model provider was actually contacted,
- whether Trino was actually contacted,
- whether fragment rewrite was default-off,
- whether opt-in + cap behavior was observed,
- whether results are local representative or live company-environment results.

## Non-goals / constraints

- Do **not** add a new rewrite strategy.
- Do **not** change P-strategy definitions.
- Do **not** make fragment rewrite default-on.
- Do **not** expand v59 into optimizer behavior changes unless a real blocker is discovered and explicitly documented.
- Do **not** fake company-environment results.
- Do **not** claim live Trino/Qwen validation unless the script/checklist actually contacted the real environment.
- If the company Trino/Qwen environment is unavailable, produce a local representative validation script/checklist and label live validation as **pending**.
- Do not commit secrets, hostnames, tokens, query text with sensitive data, or raw credentials.
- Redact environment/config details in any generated report.
- Keep validation deterministic enough for CI/local review. Live validation may be optional/manual; local representative validation must be runnable by review.
- Avoid product-file churn. This is primarily docs + validation harness.

## Acceptance criteria

- `project-iterations/genieCLI/STATUS.md` no longer presents v48/v46 as the latest current state.
- `STATUS.md` accurately records current `HEAD` through v58 and identifies v59 as real-environment validation + status repair.
- v57 behavior is documented accurately:
  - default evidence-only decompose seed,
  - `GENIE_FRAGMENT_REWRITE=1` opt-in,
  - `GENIE_FRAGMENT_REWRITE_CAP`,
  - provider/model failures preserved as reportable iterations.
- v58 behavior is documented accurately:
  - `--direct` path parity for fragment rewrite env opt-in across no-data, plan-cost, and standard-loop paths.
- A repeatable local representative validation path exists and is documented.
- Validation output clearly distinguishes:
  - local representative validation,
  - optional live company-environment validation,
  - pending live validation if unavailable.
- Targeted pytest commands for v57/v58 pass with 0 failures.
- If full suite is run, report exact pass/skip/fail counts as a this-run snapshot; do not overclaim stable skip counts.
- No new rewrite strategy is introduced.
- No product behavior is changed unless explicitly justified in the develop artifact.
- Review can verify the work from committed files plus command outputs without relying on private environment access.

## Files affected

Expected/allowed files:

- `project-iterations/genieCLI/STATUS.md`
- `project-iterations/genieCLI/archive/v57.md`
- `project-iterations/genieCLI/archive/v58.md`
- `project-iterations/genieCLI/archive/v59.md`
- `project-iterations/genieCLI/phase-reports/v59-validation.md`
- `scripts/validate_trino_research_v57_v58.py`
- `tests/test_decompose_then_iterate.py`
- `tests/test_mcp_research.py`

Evidence/source files to read but not necessarily modify:

- `.tlv4-v57-model-call-budget/artifacts/wrap_retro.producer.md`
- `.tlv4-v58-direct-fragment-optin/artifacts/wrap_retro.producer.md`
- `project-iterations/genieCLI/STATUS.md`
- Recent git log:
  - `6cd9640 fix(trino-research): fold review/wrap_retro corrections into HEAD [tlv4 commit-sync]`
  - `1581e44 chore(trino-research): close v57 task ledger`
  - `e11c472 fix(trino-research): bound v57 model calls and preserve reports`
  - `7dde3cd feat(trino-research): v56 — offline P9 fan-out structural verifier + per-strategy checklist`
  - `0da7a24 feat(trino-research): v55 — wire critical path into optimize prompt + P9 exists-to-preagg-join`
