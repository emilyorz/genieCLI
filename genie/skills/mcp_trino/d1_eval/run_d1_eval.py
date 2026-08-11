"""Run D1 analysis-coverage eval against a frozen seed.

Usage:
  python -m genie.skills.mcp_trino.d1_eval.run_d1_eval \\
    --seed eval/seed/d1_seed_v1 --out out/d1_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(seed: Path) -> dict[str, str]:
    man = seed / "MANIFEST.sha256"
    if not man.is_file():
        raise SystemExit(f"missing manifest: {man}")
    out: dict[str, str] = {}
    for line in man.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "hash  relpath" or "hash *relpath"
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, rel = parts[0], parts[-1].lstrip("*")
        out[rel.replace("\\", "/")] = digest
    return out


def _verify_manifest(seed: Path) -> str:
    expected = _load_manifest(seed)
    if not expected:
        raise SystemExit("empty MANIFEST.sha256")
    for rel, digest in sorted(expected.items()):
        fp = seed / rel
        if not fp.is_file():
            raise SystemExit(f"manifest file missing: {rel}")
        got = _sha256_file(fp)
        if got != digest:
            raise SystemExit(f"manifest mismatch: {rel}\n expected {digest}\n got      {got}")
    # overall seed fingerprint
    blob = json.dumps(expected, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def run_eval(seed: Path, out: Path, model_id: str = "static-phit-scan") -> dict[str, Any]:
    from genie.skills.mcp_trino.d1_eval.analyze import analyze_sql
    from genie.skills.mcp_trino.d1_eval.oracle_match import Finding, match_findings

    seed_hash = _verify_manifest(seed)
    qdir = seed / "queries"
    odir = seed / "oracle"
    if not qdir.is_dir() or not odir.is_dir():
        raise SystemExit("seed must contain queries/ and oracle/")

    per_query: list[dict[str, Any]] = []
    sum_tp = sum_fn = sum_fp = 0

    for qpath in sorted(qdir.glob("*.sql")):
        qid = qpath.stem
        opath = odir / f"{qid}.json"
        if not opath.is_file():
            raise SystemExit(f"missing oracle for {qid}: {opath}")
        sql = qpath.read_text(encoding="utf-8")
        oracle_doc = json.loads(opath.read_text(encoding="utf-8"))
        oracle = [Finding.from_dict(x) for x in oracle_doc.get("findings") or []]
        system = analyze_sql(sql)
        m = match_findings(oracle, system)
        sum_tp += m.tp
        sum_fn += m.fn
        sum_fp += m.fp
        per_query.append(
            {
                "query_id": qid,
                "oracle_n": len(oracle),
                "system_n": len(system),
                "tp": m.tp,
                "fn": m.fn,
                "fp": m.fp,
                "recall": round(m.recall, 4),
                "precision": round(m.precision, 4),
                "missed": [
                    {"category": f.category, "object": f.object, "note": f.note}
                    for f in m.missed
                ],
                "spurious": [
                    {"category": f.category, "object": f.object, "note": f.note}
                    for f in m.spurious
                ],
                "matched": [
                    {
                        "category": o.category,
                        "object": o.object,
                        "system_note": s.note,
                    }
                    for o, s in m.matched
                ],
            }
        )

    rec_den = sum_tp + sum_fn
    prec_den = sum_tp + sum_fp
    report = {
        "schema": "genie-d1-eval-v1",
        "seed": str(seed),
        "seed_hash": seed_hash,
        "model_id": model_id,
        "n_queries": len(per_query),
        "aggregate": {
            "tp": sum_tp,
            "fn": sum_fn,
            "fp": sum_fp,
            "recall": round((sum_tp / rec_den) if rec_den else 0.0, 4),
            "precision": round((sum_tp / prec_den) if prec_den else 0.0, 4),
        },
        "per_query": per_query,
        "caveats": [
            "Recall/precision = agreement with frozen oracle set, not ground truth.",
            "Oracle v1 may be synthetic-structural stand-in until live Opus batch is frozen.",
            "n is small; ±10pt swings can be noise.",
            "D1 analysis only — not verified apply (D2), not SQL-looks-like-Opus, not speedup %.",
            "No EXECUTE_ALL. apply remains no-op for scoring.",
        ],
        "forbidden_claims": [
            "Do not claim 80% without this scorer + frozen oracle.",
            "Do not report speedup % from this tool.",
            "Do not equate analysis coverage with applied optimization.",
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D1 analysis coverage eval")
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model-id", default="static-phit-scan")
    args = ap.parse_args(argv)
    try:
        rep = run_eval(args.seed.resolve(), args.out.resolve(), model_id=args.model_id)
    except SystemExit as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    agg = rep["aggregate"]
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(args.out),
                "n": rep["n_queries"],
                "recall": agg["recall"],
                "precision": agg["precision"],
                "seed_hash": rep["seed_hash"][:12],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
