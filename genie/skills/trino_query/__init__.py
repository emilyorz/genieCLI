"""trino_query — genieCLI skill: execute & validate SQL on local Trino."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from genie.core.arg import Arg
from genie.core.registry import BaseSkill

if TYPE_CHECKING:
    import trino.dbapi


@dataclass
class QueryResult:
    rows: list[dict]
    columns: list[str]
    duration_ms: int
    truncated: bool
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "error": self.error,
            "row_count": len(self.rows),
        }


def _clean_row(row: tuple) -> list:
    """Convert Decimal → float for JSON serialization."""
    return [float(v) if isinstance(v, Decimal) else v for v in row]


class TrinoQuerySkill(BaseSkill):
    """Execute SQL against the local Trino engine and return structured results.

    Designed to complement oracle2trino + trino_linter:
    linter finds problems → user rewrites → trino_query executes and validates.
    """

    name = "trino_query"
    description = (
        "Execute SQL against the local Trino engine and return structured results "
        "with timing. Use this to validate SQL that was written or transpiled, "
        "run EXPLAIN plans, and test queries before running them in production."
    )
    group = "trino_query"
    args = [
        Arg("sql", "str", "SQL statement(s) to execute", required=True),
        Arg("catalog", "str", "Target catalog (default: iceberg)", required=False, default="iceberg"),
        Arg("schema", "str", "Target schema (default: warehouse)", required=False, default="warehouse"),
        Arg("limit", "int", "Maximum rows to return (default: 100)", required=False, default=100),
    ]

    def run(self, ctx, sql: str = "", catalog: str = "iceberg",
            schema: str = "warehouse", limit: int = 100) -> str:
        try:
            import trino.dbapi
            conn = trino.dbapi.connect(
                host="localhost",
                port=8085,
                user="trino",
                catalog=catalog,
                schema=schema,
                http_scheme="http",
            )
            cur = conn.cursor()
            t0 = time.monotonic()
            cur.execute(sql)
            t_ms = int((time.monotonic() - t0) * 1000)
            desc = cur.description or []
            cols = [c[0] for c in desc]
            rows = []
            for row in cur.fetchall():
                rows.append(dict(zip(cols, _clean_row(row))))
                if len(rows) >= limit:
                    rows = rows[:limit]
                    break
            conn.close()
            result = QueryResult(
                rows=rows, columns=cols, duration_ms=t_ms,
                truncated=len(rows) >= limit,
            )
            out = ctx.output
            if cols:
                out.progress(f"[{len(rows)} rows · {t_ms}ms]")
                for r in rows[:5]:
                    out.print("  " + "  ".join(str(r[c]) for c in cols))
                if len(rows) > 5:
                    out.print(f"  [dim]({len(rows)} rows returned)[/dim]")
            else:
                out.print(f"[green]OK[/green] ({t_ms}ms)")
            return json.dumps(result.to_json(), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({
                "error": str(exc),
                "rows": [],
                "columns": [],
                "duration_ms": 0,
                "truncated": False,
            }, ensure_ascii=False)


class TrinoExplainSkill(BaseSkill):
    """Run EXPLAIN on SQL and return the query plan."""

    name = "trino_explain"
    description = "Run EXPLAIN on SQL and return the query plan for analysis."
    group = "trino_query"
    args = [
        Arg("sql", "str", "SQL statement to EXPLAIN", required=True),
        Arg("catalog", "str", "Target catalog (default: iceberg)", required=False, default="iceberg"),
        Arg("schema", "str", "Target schema (default: warehouse)", required=False, default="warehouse"),
        Arg("type", "str", "EXPLAIN type (default: formatted)",
            required=False, default="formatted",
            choices=["logical", "distributed", "formatted", "json"]),
    ]

    def run(self, ctx, sql: str = "", catalog: str = "iceberg",
            schema: str = "warehouse", type: str = "logical") -> str:
        # Trino 480: EXPLAIN syntax is EXPLAIN (TYPE logical|distributed) <sql>
        # NOTE: FORMAT is not supported in this version
        explain_sql = f"EXPLAIN (TYPE {type.upper()}) {sql}"
        try:
            import trino.dbapi
            conn = trino.dbapi.connect(
                host="localhost", port=8085, user="trino",
                catalog=catalog, schema=schema, http_scheme="http",
            )
            cur = conn.cursor()
            cur.execute(explain_sql)
            plan_rows = cur.fetchall()
            conn.close()
            plan_text = "\n".join(r[0] for r in plan_rows) if plan_rows else ""
            out = ctx.output
            out.print(f"[dim]EXPLAIN {type}[/dim]")
            for line in plan_text.split("\n")[:20]:
                out.print("  " + line)
            if len(plan_text.split("\n")) > 20:
                out.print(f"  [dim]({len(plan_text.split(chr(10)))} lines truncated)[/dim]")
            return json.dumps({"plan": plan_text, "type": type, "sql": sql}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc), "plan": ""}, ensure_ascii=False)


def register(registry) -> None:
    registry.register(TrinoQuerySkill())
    registry.register(TrinoExplainSkill())