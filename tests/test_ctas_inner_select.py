"""Tests for extract_ctas_inner_select — strips CREATE TABLE ... AS to its inner query.

The CTAS optimization value lives in the inner SELECT, so the write-analysis path
extracts it and runs the non-executing optimization steps on the query, not the
DDL shell. These tests pin: CTAS variants extract; non-CTAS / malformed return
None (caller falls back to the whole statement); never raises.
"""
from genie.core.sql_extraction import extract_ctas_inner_select


def test_simple_ctas_extracts_inner_select():
    inner = extract_ctas_inner_select("CREATE TABLE a.b AS SELECT x FROM t WHERE x > 0")
    assert inner is not None
    assert inner.upper().startswith("SELECT")
    assert "FROM t" in inner


def test_or_replace_with_leading_cte_extracts_with_body():
    inner = extract_ctas_inner_select(
        "CREATE OR REPLACE TABLE a.b AS WITH c AS (SELECT 1) SELECT * FROM c"
    )
    assert inner is not None
    assert inner.upper().startswith("WITH")
    assert "SELECT * FROM c" in inner


def test_multiline_ctas_extracts_inner():
    inner = extract_ctas_inner_select(
        "CREATE TABLE analytics.daily AS\n"
        "SELECT o.id FROM orders o JOIN customers c ON o.cid = c.id"
    )
    assert inner is not None
    assert inner.upper().startswith("SELECT")
    assert "JOIN customers" in inner


def test_insert_is_not_ctas():
    assert extract_ctas_inner_select("INSERT INTO x SELECT * FROM y") is None


def test_plain_select_is_not_ctas():
    assert extract_ctas_inner_select("SELECT * FROM t") is None


def test_create_table_without_as_is_not_ctas():
    assert extract_ctas_inner_select("CREATE TABLE z (id INT)") is None


def test_garbage_returns_none_does_not_raise():
    assert extract_ctas_inner_select("not even sql ;;;") is None


def test_empty_returns_none():
    assert extract_ctas_inner_select("") is None
    assert extract_ctas_inner_select("   ") is None
