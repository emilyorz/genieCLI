# v59 validation report — v57/v58 `/trino-research`

## Purpose

Record the validation boundary for v59: local representative checks are allowed in this repo; live company Trino/Qwen validation must be explicitly authorized and must not be invented.

## Local representative validation

Command:

```bash
python scripts/validate_trino_research_v57_v58.py --offline
```

This checks:

- STATUS mentions current v57/v58/v59 facts and safety knobs;
- `archive/v57.md`, `archive/v58.md`, and `archive/v59.md` exist;
- targeted v57/v58 pytest surfaces pass:
  - `tests/test_decompose_then_iterate.py`
  - `tests/test_mcp_research.py`

## Optional broader local sweep

```bash
python scripts/validate_trino_research_v57_v58.py --offline --broad
```

Adds:

- `tests/test_strategy_verify.py`
- `tests/test_step_trace.py`
- `tests/test_trino_optimize.py`
- `tests/test_critical_path.py`

## Live company validation

Pending until Sam runs or authorizes a real company Trino/Qwen environment check.

Rules:

- Do not record live validation as passed unless Trino/Qwen was actually contacted.
- Redact private hostnames, credentials, and query text.
- Preserve whether fragment rewrite was default-off and whether opt-in + cap behavior was observed.
