"""Tests for trino_query skill."""
from __future__ import annotations

from unittest.mock import MagicMock

from genie.skills.trino_query import TrinoQuerySkill, TrinoExplainSkill


class TestTrinoQuerySkill:
    def test_skills_have_required_fields(self):
        q = TrinoQuerySkill()
        assert q.name == "trino_query"
        assert q.group == "trino_query"
        assert len(q.args) == 4
        arg_names = [a.name for a in q.args]
        assert "sql" in arg_names
        assert "catalog" in arg_names
        assert "schema" in arg_names
        assert "limit" in arg_names

    def test_explain_skill_fields(self):
        e = TrinoExplainSkill()
        assert e.name == "trino_explain"
        assert e.group == "trino_query"
        assert len(e.args) == 4