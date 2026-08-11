# D1 baseline — 2026-08-11 (post Fable develop must-fixes)

Seed: `eval/seed/d1_seed_v1` (n=23; 20 pattern + 3 adversarial near-miss)  
Report: `eval/reports/d1_report_baseline.json`  
`oracle_provenance`: **synthetic_v0**  
`metric_kind`: **harness_self_consistency**

## Aggregate (static-phit-scan)

| field | value |
|---|---|
| harness_self_consistency_recall | **1.0** |
| harness_self_consistency_precision | **1.0** |
| tp/fn/fp | 26/0/0 |
| product D1 recall/precision | **N/A** until `opus_adjudicated_v*` oracle |

**Do not quote these numbers as “Opus analysis coverage” or “80%”.**

## Detector provenance

| detector / signal | source | new this run? |
|---|---|---|
| NON_SARGABLE (P1) | `phit_scan` P1 | reused |
| CORRELATED_SUBQUERY (P9) | `phit_scan` P9 | reused |
| REDUNDANT_CTE_JOIN (P10) | `phit_scan` P10 | reused |
| LEADING_WILDCARD_LIKE (P3, leading-only) | `phit_scan` P3 tightened | improved |
| SELECT_STAR_WIDE | `d1_eval.analyze` | new mapping |
| CARTESIAN_RISK | `d1_eval.analyze` | new mapping |
| taxonomy + matcher + eval harness | `d1_eval/*` | **new** |

## Fable must-fixes landed
1. metric_kind + oracle_provenance + harness_self_consistency_* fields  
2. seed hygiene (queries/oracle/manifest only) + freeze test  
3. adversarial q21–q23 empty-oracle near-misses  
4. detector provenance table  
5. CI manifest recompute test  
6. P3 leading-wildcard-only (trailing `foo%` no longer false hit)

## Caveats
synthetic_v0 ≠ Opus 80%. D1 only. No EXECUTE_ALL. No speedup %.

## Re-run
```bash
python -m genie.skills.mcp_trino.d1_eval.run_d1_eval --seed eval/seed/d1_seed_v1 --out out/d1_report.json
pytest tests/test_oracle_match.py tests/test_d1_eval.py -q
```
