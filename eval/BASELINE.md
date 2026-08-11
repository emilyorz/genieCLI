# D1 baseline — 2026-08-11

Commit tip (worktree): see git log after merge.  
Seed: `eval/seed/d1_seed_v1` (n=20)  
Report: `out/d1_report.json`

## Aggregate (static-phit-scan)

| metric | value |
|---|---|
| recall | **1.00** |
| precision | **1.00** |
| tp/fn/fp | 26 / 0 / 0 |

## Caveats (mandatory)

1. This oracle is a **synthetic structural stand-in** labeled to exercise current `phit_scan` + light extras — **not** a frozen multi-run Opus 4.6 adjudication set.
2. Therefore **1.00 does NOT mean** “we analyze 80% of what Opus analyzes.” It means: **harness + scorer + seed loop works** and current detectors cover this seed.
3. Next: replace/expand oracle with real Opus-labeled findings (Fable Slice 1 multi-run freeze); then report honest recall@precision.
4. D1 only — not D2 apply, not SQL-lookalike, not speedup %.
5. No EXECUTE_ALL; apply remains no-op for scoring.

## How to re-run

```bash
python -m genie.skills.mcp_trino.d1_eval.run_d1_eval \
  --seed eval/seed/d1_seed_v1 --out out/d1_report.json
pytest tests/test_oracle_match.py tests/test_d1_eval.py -q
```
