"""Tests for trino_query skill + connection profiles."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.trino_query import TrinoQuerySkill, TrinoExplainSkill, QueryResult, QueryMetrics, _clean_row, _extract_metrics, _human_bytes
from genie.skills.trino_query.connection import (
    TrinoProfile,
    list_profiles,
    get_active_name,
    get_active_profile,
    set_active,
    add_profile,
    remove_profile,
    status_line,
    CONFIG_PATH,
)


# ── QueryResult ───────────────────────────────────────────────────────────────

class TestQueryResult:
    def test_to_json_fields(self):
        r = QueryResult(rows=[{"a": 1}], columns=["a"], duration_ms=42, truncated=False)
        d = r.to_json()
        assert d["row_count"] == 1
        assert d["duration_ms"] == 42
        assert d["truncated"] is False
        assert d["error"] is None

    def test_to_json_with_error(self):
        r = QueryResult(rows=[], columns=[], duration_ms=0, truncated=False, error="boom")
        d = r.to_json()
        assert d["error"] == "boom"
        assert d["row_count"] == 0


# ── _clean_row ────────────────────────────────────────────────────────────────

class TestCleanRow:
    def test_decimal_to_float(self):
        from decimal import Decimal
        result = _clean_row((Decimal("3.14"), "hello", 42, None))
        assert result == [3.14, "hello", 42, None]

    def test_no_decimal(self):
        result = _clean_row(("a", 1, True))
        assert result == ["a", 1, True]


# ── Skill fields ──────────────────────────────────────────────────────────────

class TestSkillFields:
    def test_query_skill(self):
        q = TrinoQuerySkill()
        assert q.name == "trino_query"
        assert q.group == "trino_query"
        assert len(q.args) == 4
        arg_names = [a.name for a in q.args]
        assert "sql" in arg_names
        assert "catalog" in arg_names

    def test_explain_skill(self):
        e = TrinoExplainSkill()
        assert e.name == "trino_explain"
        assert len(e.args) == 4
        type_arg = [a for a in e.args if a.name == "type"][0]
        assert "distributed" in type_arg.choices


# ── Skill run (mocked) ───────────────────────────────────────────────────────

class TestSkillRunMocked:
    def _make_ctx(self):
        output = MagicMock()
        output.__class__.__name__ = "HumanSink"
        ctx = MagicMock()
        ctx.output = output
        return ctx

    def _mock_cursor(self, description, rows, stats=None):
        mock_cur = MagicMock()
        mock_cur.description = description
        mock_cur.fetchall.return_value = rows
        mock_cur.stats = stats or {"cpuTimeMillis": 5, "wallTimeMillis": 10, "peakMemoryBytes": 1024,
                                    "physicalInputBytes": 2048, "processedRows": len(rows), "totalSplits": 4}
        mock_cur.query_id = "test_query_123"
        return mock_cur

    @patch("genie.skills.trino_query.get_active_profile")
    def test_query_run_success(self, mock_profile):
        mock_conn = MagicMock()
        mock_cur = self._mock_cursor([("name",), ("val",)], [("Alice", 100), ("Bob", 200)])
        mock_conn.cursor.return_value = mock_cur
        mock_profile.return_value = MagicMock()
        mock_profile.return_value.connect.return_value = mock_conn

        skill = TrinoQuerySkill()
        ctx = self._make_ctx()
        result = json.loads(skill.run(ctx, sql="SELECT 1"))
        assert result["row_count"] == 2
        assert result["error"] is None
        assert result["columns"] == ["name", "val"]
        assert result["query_id"] == "test_query_123"
        assert result["metrics"]["cpu_time_ms"] == 5
        assert result["metrics"]["peak_memory_human"] == "1.0KB"

    @patch("genie.skills.trino_query.get_active_profile")
    def test_query_run_error(self, mock_profile):
        mock_profile.return_value.connect.side_effect = Exception("connection refused")
        skill = TrinoQuerySkill()
        ctx = self._make_ctx()
        result = json.loads(skill.run(ctx, sql="SELECT 1"))
        assert "connection refused" in result["error"]
        assert result["rows"] == []

    @patch("genie.skills.trino_query.get_active_profile")
    def test_query_run_no_description(self, mock_profile):
        mock_conn = MagicMock()
        mock_cur = self._mock_cursor(None, [])
        mock_conn.cursor.return_value = mock_cur
        mock_profile.return_value = MagicMock()
        mock_profile.return_value.connect.return_value = mock_conn

        skill = TrinoQuerySkill()
        ctx = self._make_ctx()
        result = json.loads(skill.run(ctx, sql="CREATE TABLE x (a INT)"))
        assert result["row_count"] == 0
        assert result["columns"] == []
        assert result["metrics"] is not None

    @patch("genie.skills.trino_query.get_active_profile")
    def test_explain_run_success(self, mock_profile):
        mock_conn = MagicMock()
        mock_cur = self._mock_cursor(None, [("Fragment 0 [SOURCE]",), ("  Output layout: [a]",)])
        mock_conn.cursor.return_value = mock_cur
        mock_profile.return_value = MagicMock()
        mock_profile.return_value.connect.return_value = mock_conn

        skill = TrinoExplainSkill()
        ctx = self._make_ctx()
        result = json.loads(skill.run(ctx, sql="SELECT 1"))
        assert "Fragment 0" in result["plan"]


# ── Metrics helpers ─────────────────────────────────────────────────────────

class TestMetrics:
    def test_extract_metrics(self):
        stats = {"cpuTimeMillis": 42, "wallTimeMillis": 100, "peakMemoryBytes": 4096,
                 "physicalInputBytes": 8192, "processedRows": 50, "totalSplits": 8,
                 "spilledBytes": 1024}
        m = _extract_metrics(stats)
        assert m.cpu_time_ms == 42
        assert m.peak_memory_bytes == 4096
        assert m.spilled_bytes == 1024

    def test_extract_metrics_empty(self):
        m = _extract_metrics({})
        assert m.cpu_time_ms == 0

    def test_summary_line(self):
        m = QueryMetrics(cpu_time_ms=42, wall_time_ms=100, peak_memory_bytes=1048576,
                         physical_input_bytes=2097152, processed_rows=1000, total_splits=16)
        line = m.summary_line()
        assert "cpu=42ms" in line
        assert "mem=1.0MB" in line
        assert "splits=16" in line

    def test_summary_line_with_spill(self):
        m = QueryMetrics(spilled_bytes=1048576)
        line = m.summary_line()
        assert "spill=1.0MB" in line

    def test_human_bytes(self):
        assert _human_bytes(0) == "0B"
        assert _human_bytes(500) == "500B"
        assert _human_bytes(1024) == "1.0KB"
        assert _human_bytes(1048576) == "1.0MB"
        assert _human_bytes(1073741824) == "1.0GB"


# ── Connection profiles ───────────────────────────────────────────────────────

class TestConnectionProfiles:
    @pytest.fixture(autouse=True)
    def use_temp_config(self, tmp_path, monkeypatch):
        """Redirect CONFIG_PATH to temp dir so tests don't touch real config."""
        temp_config = tmp_path / "trino.json"
        monkeypatch.setattr("genie.skills.trino_query.connection.CONFIG_PATH", temp_config)
        yield temp_config

    def test_default_profile_created(self):
        profiles = list_profiles()
        assert "local" in profiles
        assert profiles["local"].host == "localhost"

    def test_get_active_name_default(self):
        assert get_active_name() == "local"

    def test_add_and_switch_profile(self):
        add_profile("staging", TrinoProfile(host="staging.example.com", port=8080, label="Staging"))
        profiles = list_profiles()
        assert "staging" in profiles
        assert profiles["staging"].host == "staging.example.com"

        assert set_active("staging") is True
        assert get_active_name() == "staging"

    def test_switch_nonexistent_profile(self):
        assert set_active("nonexistent") is False

    def test_remove_profile(self):
        add_profile("temp", TrinoProfile(host="temp.local"))
        assert remove_profile("temp") is True
        assert "temp" not in list_profiles()

    def test_cannot_remove_active_profile(self):
        assert remove_profile("local") is False

    def test_status_line_format(self):
        line = status_line()
        assert "local" in line
        assert "localhost" in line

    def test_profile_display_name(self):
        p = TrinoProfile(host="example.com", port=443, scheme="https", label="Prod")
        assert "https://example.com:443" in p.display_name()
        assert "Prod" in p.display_name()

    def test_profile_display_name_no_label(self):
        p = TrinoProfile(host="localhost", port=8085, scheme="http")
        dn = p.display_name()
        assert "http://localhost:8085" in dn
        assert "(" not in dn

    def test_get_active_profile_defaults(self):
        p = get_active_profile()
        assert p.host == "localhost"
        assert p.port == 8085
