"""Integration tests for trino_query — requires live Trino at localhost:8085.

Skip if Trino is not available.
"""
from __future__ import annotations

import json
import os

import pytest

# Skip entire module if Trino is not reachable
try:
    import urllib.request
    urllib.request.urlopen("http://localhost:8085/v1/info", timeout=2)
    TRINO_AVAILABLE = True
except Exception:
    TRINO_AVAILABLE = False

pytestmark = pytest.mark.skipif(not TRINO_AVAILABLE, reason="Trino not running at localhost:8085")


@pytest.fixture
def ctx():
    from genie.output.machine import MachineSink
    from genie.core.context import SkillContext
    return SkillContext(provider=None, output=MachineSink(), config={}, session=None)


class TestTrinoQueryIntegration:
    def test_select_one(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECT 1 AS n"))
        assert result["error"] is None
        assert result["row_count"] == 1
        assert result["rows"][0]["n"] == 1

    def test_select_employees(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECT name, dept FROM iceberg.warehouse.employees ORDER BY name LIMIT 3"))
        assert result["error"] is None
        assert result["row_count"] == 3
        assert result["rows"][0]["name"] == "Alice Chen"

    def test_select_with_decimal(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECT salary FROM iceberg.warehouse.employees ORDER BY salary DESC LIMIT 1"))
        assert result["error"] is None
        assert isinstance(result["rows"][0]["salary"], float)
        assert result["rows"][0]["salary"] == 130000.0

    def test_bad_sql(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECTZ BADBADBAD"))
        assert result["error"] is not None
        assert result["rows"] == []

    def test_explain_distributed(self, ctx):
        from genie.skills.trino_query import TrinoExplainSkill
        skill = TrinoExplainSkill()
        result = json.loads(skill.run(ctx, sql="SELECT COUNT(*) FROM iceberg.warehouse.employees", type="distributed"))
        assert "Fragment" in result["plan"]

    def test_schema_catalogs(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SHOW CATALOGS"))
        catalogs = [r["Catalog"] for r in result["rows"]]
        assert "iceberg" in catalogs
        assert "memory" in catalogs

    def test_schema_tables(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SHOW TABLES FROM iceberg.warehouse"))
        tables = [r["Table"] for r in result["rows"]]
        assert "employees" in tables
        assert "orders" in tables

    def test_describe_columns(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="DESCRIBE iceberg.warehouse.employees"))
        columns = [r["Column"] for r in result["rows"]]
        assert "name" in columns
        assert "salary" in columns
        assert "dept" in columns

    def test_limit_enforcement(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECT * FROM iceberg.warehouse.employees", limit=3))
        assert result["row_count"] <= 3
        assert result["truncated"] is True

    def test_memory_catalog(self, ctx):
        from genie.skills.trino_query import TrinoQuerySkill
        skill = TrinoQuerySkill()
        result = json.loads(skill.run(ctx, sql="SELECT * FROM memory.test.numbers", catalog="memory", schema="test"))
        assert result["error"] is None
        assert result["row_count"] == 5
