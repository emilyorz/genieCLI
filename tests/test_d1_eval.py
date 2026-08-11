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
    assert len(list((SEED / "queries").glob("*.sql"))) == 20


def test_analyze_sql_non_sargable_join():
    sql = """SELECT a.id FROM orders a JOIN customers b ON COALESCE(a.cust_id,0)=b.id"""
    findings = analyze_sql(sql)
    cats = {f.category for f in findings}
    assert "NON_SARGABLE" in cats


def test_analyze_sql_correlated_exists():
    sql = """SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM lineitem l WHERE l.order_id=o.id)"""
    findings = analyze_sql(sql)
    assert any(f.category == "CORRELATED_SUBQUERY" for f in findings)


def test_run_d1_eval_end_to_end(tmp_path: Path):
    out = tmp_path / "d1_report.json"
    rep = run_eval(SEED, out, model_id="static-phit-scan")
    assert out.is_file()
    assert rep["n_queries"] == 20
    assert "recall" in rep["aggregate"]
    assert "precision" in rep["aggregate"]
    assert rep["aggregate"]["precision"] >= 0.0
    assert "caveats" in rep and len(rep["caveats"]) >= 1
    # forbidden claims present as documentation
    assert any("speedup" in x.lower() or "80%" in x for x in rep["forbidden_claims"])


def test_manifest_mismatch_fails(tmp_path: Path):
    # copy seed lightly by pointing to real seed then break via wrong out path logic —
    # call verify by corrupting a temp seed
    import hashlib
    import shutil

    s = tmp_path / "seed"
    shutil.copytree(SEED, s)
    q = next((s / "queries").glob("*.sql"))
    q.write_text(q.read_text() + "\n-- tamper\n")
    out = tmp_path / "r.json"
    with pytest.raises(SystemExit):
        run_eval(s, out)
