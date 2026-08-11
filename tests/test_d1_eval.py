"""D1 eval harness smoke + manifest fail-closed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genie.skills.mcp_trino.d1_eval.analyze import analyze_sql
from genie.skills.mcp_trino.d1_eval.run_d1_eval import run_eval


SEED = Path(__file__).resolve().parents[1] / "eval" / "seed" / "d1_seed_v1"


def test_seed_exists():
    assert SEED.is_dir()
    assert (SEED / "MANIFEST.sha256").is_file()
    n = len(list((SEED / "queries").glob("*.sql")))
    assert n >= 20


def test_seed_dir_only_queries_oracle_manifest():
    allowed = {"queries", "oracle", "MANIFEST.sha256"}
    names = {p.name for p in SEED.iterdir()}
    # only allowed top-level entries
    assert names <= allowed or names == {"queries", "oracle", "MANIFEST.sha256"}
    for p in SEED.rglob("*"):
        if p.is_file() and p.name != "MANIFEST.sha256":
            rel = str(p.relative_to(SEED)).replace("\\", "/")
            assert rel.startswith("queries/") or rel.startswith("oracle/"), rel


def test_manifest_freeze_enforced():
    """CI freeze: recompute hashes must match MANIFEST.sha256."""
    import hashlib

    expected = {}
    for line in (SEED / "MANIFEST.sha256").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        expected[parts[-1].lstrip("*")] = parts[0]
    assert expected
    for rel, digest in expected.items():
        got = hashlib.sha256((SEED / rel).read_bytes()).hexdigest()
        assert got == digest, rel


def test_analyze_sql_non_sargable_join():
    sql = """SELECT a.id FROM orders a JOIN customers b ON COALESCE(a.cust_id,0)=b.id"""
    findings = analyze_sql(sql)
    cats = {f.category for f in findings}
    assert "NON_SARGABLE" in cats


def test_analyze_sql_correlated_exists():
    sql = """SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM lineitem l WHERE l.order_id=o.id)"""
    findings = analyze_sql(sql)
    assert any(f.category == "CORRELATED_SUBQUERY" for f in findings)


def test_adversarial_clean_equijoin_no_nonsargable():
    sql = """SELECT a.id, b.name FROM a JOIN b ON a.id = b.id WHERE a.region = 'TW'"""
    findings = analyze_sql(sql)
    assert not any(f.category == "NON_SARGABLE" for f in findings)


def test_adversarial_trailing_like_not_leading_wildcard():
    sql = """SELECT id FROM t WHERE name LIKE 'foo%'"""
    findings = analyze_sql(sql)
    assert not any(f.category == "LEADING_WILDCARD_LIKE" for f in findings)


def test_run_d1_eval_end_to_end(tmp_path: Path):
    out = tmp_path / "d1_report.json"
    rep = run_eval(SEED, out, model_id="static-phit-scan")
    assert out.is_file()
    assert rep["n_queries"] >= 20
    assert rep.get("oracle_provenance") == "synthetic_v0"
    assert rep.get("metric_kind") == "harness_self_consistency"
    assert "harness_self_consistency_recall" in rep["aggregate"]
    assert rep["aggregate"].get("recall_is_product_metric") is False
    assert "caveats" in rep and len(rep["caveats"]) >= 1
    assert any("speedup" in x.lower() or "80%" in x for x in rep["forbidden_claims"])


def test_manifest_mismatch_fails(tmp_path: Path):
    import shutil

    s = tmp_path / "seed"
    shutil.copytree(SEED, s)
    q = next((s / "queries").glob("*.sql"))
    q.write_text(q.read_text() + "\n-- tamper\n")
    out = tmp_path / "r.json"
    with pytest.raises(SystemExit):
        run_eval(s, out)
