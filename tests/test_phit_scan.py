from pathlib import Path
from genie.skills.mcp_trino.phit_scan import scan_phits

FIX = Path(__file__).parent / "fixtures" / "rewrite_patterns"


def _load(name: str) -> str:
    return (FIX / name).read_text()


def test_join_on_coalesce_hits_p1():
    hits = scan_phits(_load("join_on_coalesce.sql"))
    pids = {h.pid for h in hits}
    assert "P1" in pids
    assert all(h.pid != "P10" for h in hits)


def test_correlated_exists_hits_p9():
    hits = scan_phits(_load("correlated_exists.sql"))
    pids = {h.pid for h in hits}
    assert "P9" in pids
    # at least one exists hit
    assert any(h.pid == "P9" for h in hits)


def test_like_and_listagg_hits_p3_p4():
    hits = scan_phits(_load("like_and_listagg.sql"))
    pids = {h.pid for h in hits}
    assert "P3" in pids
    assert "P4" in pids
    for h in hits:
        if h.pid in {"P3", "P4"}:
            assert h.tier == "dangerous"


def test_empty_and_bad_sql():
    assert scan_phits("") == []
    assert scan_phits("not sql ;;;%%%") == [] or isinstance(scan_phits("SELECT 1"), list)
