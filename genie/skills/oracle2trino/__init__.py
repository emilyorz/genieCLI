"""Oracle to Trino SQL migration skill package."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from genie.core.arg import Arg
from genie.core.registry import BaseSkill

MAX_SQL_DISPLAY = 3000

_DB: dict | None = None


def _load_db() -> dict:
    global _DB
    if _DB is None:
        yaml_path = Path(__file__).parent / "data" / "oracle_trino_functions.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            _DB = yaml.safe_load(f)
    return _DB


def _truncate(text: str, limit: int = MAX_SQL_DISPLAY) -> str:
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _sqlglot_transpile(sql: str) -> tuple[str, list[str]]:
    try:
        import sqlglot
        import sqlglot.errors as sge

        warnings: list[str] = []
        results = sqlglot.transpile(
            sql, read="oracle", write="trino",
            error_level=sge.ErrorLevel.IGNORE,
        )
        transpiled = results[0] if results else sql

        known_gaps = [
            ("ROWNUM",           "ROWNUM not converted — rewrite as ROW_NUMBER() subquery or FETCH FIRST n ROWS"),
            ("LISTAGG",          "LISTAGG not converted — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("MONTHS_BETWEEN",   "MONTHS_BETWEEN not converted — rewrite as DATE_DIFF('month', d2, d1)"),
            ("WM_CONCAT",        "WM_CONCAT not converted — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("CONNECT BY",       "CONNECT BY may not be fully converted — verify recursive CTE output"),
            ("PIVOT",            "PIVOT may be dropped or incorrect — rewrite as CASE-WHEN aggregation"),
            ("UNPIVOT",          "UNPIVOT not converted — rewrite as CROSS JOIN UNNEST"),
            ("EXECUTE IMMEDIATE","EXECUTE IMMEDIATE not converted — move to Python layer"),
        ]
        upper_out = transpiled.upper()
        for keyword, msg in known_gaps:
            if keyword in upper_out:
                warnings.append(f"⚠️  {msg}")
        return transpiled, warnings
    except ImportError:
        return sql, ["sqlglot not installed — run: pip install sqlglot"]
    except Exception as exc:
        return sql, [f"sqlglot transpile error: {exc}"]


class TranspileSQL(BaseSkill):
    name = "transpile_sql"
    description = (
        "Run sqlglot to mechanically transpile Oracle SQL to Trino SQL. "
        "Handles ~50% of common syntax differences automatically. "
        "Returns transpiled SQL + warnings for constructs that need manual attention."
    )
    group = "oracle2trino"
    args = [Arg(name="sql", type="str", description="Oracle SQL statement(s) to transpile", required=True)]

    def run(self, sql: str = "") -> str:
        transpiled, warnings = _sqlglot_transpile(sql)
        lines = ["=== sqlglot Oracle→Trino Transpile ===", "",
                 "--- Input ---", _truncate(sql), "",
                 "--- Transpiled Output ---", _truncate(transpiled), ""]
        if warnings:
            lines.append("--- Warnings (needs AI review) ---")
            lines.extend(warnings)
        else:
            lines.append("✅ No known gaps detected in transpiled output.")
        return "\n".join(lines)


class LookupOracleFunction(BaseSkill):
    name = "lookup_oracle_function"
    description = "Look up how to convert an Oracle SQL function to Trino equivalent."
    group = "oracle2trino"
    args = [Arg(name="oracle_name", type="str",
                description="Oracle function name (e.g. NVL, SYSDATE, ROWNUM)",
                required=True)]

    def run(self, oracle_name: str = "") -> str:
        db = _load_db()
        needle = oracle_name.strip().upper()
        funcs = db.get("functions", [])
        exact = [e for e in funcs if needle == str(e.get("oracle", "")).upper()]
        results = exact or [e for e in funcs if needle in str(e.get("oracle", "")).upper()]
        if not results:
            return f"No mapping found for '{oracle_name}'."
        lines = []
        for r in results[:5]:
            lines.append(f"Oracle: {r['oracle']}")
            lines.append(f"Trino:  {r.get('trino') or 'No direct equivalent'}")
            if r.get("example"):
                lines.append(f"Example: {r['example']}")
            if r.get("notes"):
                lines.append(f"Notes:  {r['notes']}")
            lines.append("")
        return "\n".join(lines).strip()


class LookupOracleType(BaseSkill):
    name = "lookup_oracle_type"
    description = "Look up how to convert an Oracle data type to Trino equivalent."
    group = "oracle2trino"
    args = [Arg(name="oracle_type", type="str",
                description="Oracle data type name (e.g. VARCHAR2, NUMBER, DATE)",
                required=True)]

    def run(self, oracle_type: str = "") -> str:
        db = _load_db()
        needle = oracle_type.strip().upper()
        for entry in db.get("types", []):
            if needle == str(entry.get("oracle", "")).upper():
                result = f"Oracle: {entry['oracle']}\nTrino:  {entry.get('trino', 'Unknown')}"
                if entry.get("notes"):
                    result += f"\nNotes:  {entry['notes']}"
                return result
        return f"No type mapping found for '{oracle_type}'."


class ListTrinoLimitations(BaseSkill):
    name = "list_trino_limitations"
    description = "List all known Trino hard limits that affect Oracle SP migration."
    group = "oracle2trino"
    args = []

    def run(self) -> str:
        db = _load_db()
        limits = db.get("trino_limitations", [])
        if not limits:
            return "No limitations data found."
        lines = ["Trino Hard Limits:"]
        for i, lim in enumerate(limits, 1):
            lines.append(f"  {i}. {lim}")
        return "\n".join(lines)


class AnalyzeOracleSP(BaseSkill):
    name = "analyze_oracle_sp"
    description = (
        "Full analysis of an Oracle stored procedure for Trino migration. "
        "Runs sqlglot transpile, detects PL/SQL constructs, and gives structured guidance."
    )
    group = "oracle2trino"
    args = [
        Arg(name="sql", type="str", description="Oracle PL/SQL or SQL text to analyze", required=True),
        Arg(name="connector", type="str",
            description="Target Trino connector (hive/iceberg/delta/generic)",
            required=False, default="iceberg",
            choices=["hive", "iceberg", "delta", "generic"]),
    ]

    def run(self, sql: str = "", connector: str = "iceberg") -> str:
        db = _load_db()
        limits = db.get("trino_limitations", [])
        limits_text = "\n".join(f"- {lim}" for lim in limits)
        transpiled, transpile_warnings = _sqlglot_transpile(sql)
        changed = transpiled.strip() != sql.strip()

        transpile_section = "--- Step 1: sqlglot Auto-Transpile ---\n"
        if changed:
            transpile_section += f"✅ sqlglot converted some constructs.\n\nTranspiled output:\n{_truncate(transpiled)}\n"
        else:
            transpile_section += "⚠️  sqlglot made no changes.\n"
        if transpile_warnings:
            transpile_section += "\nRemaining issues after sqlglot:\n"
            for w in transpile_warnings:
                transpile_section += f"  {w}\n"

        upper_sql = sql.upper()
        flags = []
        plsql_patterns = [
            (r'(?:^|\s)BEGIN\b',       "BEGIN",            "PL/SQL block — wrap queries in Python"),
            (r'(?:^|\s)DECLARE\b',     "DECLARE",          "Variable declarations — replace with CTEs or Python variables"),
            (r'\bCURSOR\b',            "CURSOR",           "Cursor — rewrite as Python loop + multiple Trino queries"),
            (r'\bFOR\s+\w+\s+IN\b',   "FOR...IN",         "FOR loop — move to Python orchestration"),
            (r'(?:^|\s)LOOP\b',        "LOOP",             "LOOP — move to Python orchestration"),
            (r'\bEXCEPTION\b',        "EXCEPTION",        "Exception handling — move to Python try/except"),
            (r'\bEXECUTE\s+IMMEDIATE\b', "EXECUTE IMMEDIATE", "Dynamic SQL — move to Python"),
            (r'\bDBMS_\w+',           "DBMS_*",           "Oracle DBMS package — no Trino equivalent"),
            (r'\bCONNECT\s+BY\b',    "CONNECT BY",       "Hierarchical query — rewrite as WITH RECURSIVE CTE"),
            (r'\bROWNUM\b',           "ROWNUM",           "ROWNUM — rewrite as ROW_NUMBER() or FETCH FIRST n ROWS"),
            (r'\bMERGE\s+INTO\b',    "MERGE INTO",       f"MERGE — Iceberg only, verify connector={connector}"),
            (r'\(\+\)',               "(+)",              "Oracle outer join (+) — rewrite as ANSI LEFT/RIGHT JOIN"),
            (r'\bLISTAGG\b',         "LISTAGG",          "LISTAGG — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            (r'\bMONTHS_BETWEEN\b',  "MONTHS_BETWEEN",   "MONTHS_BETWEEN — rewrite as DATE_DIFF('month', d2, d1)"),
        ]
        for pattern, label, message in plsql_patterns:
            if re.search(pattern, upper_sql):
                flags.append(f"⚠️  [{label}] {message}")

        flags_text = "\n".join(flags) if flags else "✅ No PL/SQL constructs detected — likely pure SQL."

        connector_notes = {
            "hive":    "Hive: read-heavy, no UPDATE/DELETE/MERGE, INSERT only via CTAS.",
            "iceberg": "Iceberg: INSERT/UPDATE/DELETE supported, limited MERGE (Trino 420+).",
            "delta":   "Delta: INSERT supported, limited UPDATE/DELETE, no MERGE.",
            "generic": "Generic: assume read-only, no DML.",
        }.get(connector, "")

        return f"""=== Oracle SP Migration Analysis ===

Target connector : {connector}
Connector notes  : {connector_notes}

{transpile_section}
--- Step 2: Detected Constructs ---
{flags_text}

--- Trino Hard Limits (reference) ---
{limits_text}

--- Original SQL ---
{_truncate(sql)}

--- Instructions ---
Start from the sqlglot transpiled output (not the original Oracle SQL).
Fix each remaining warning, annotate every change: -- [Oracle→Trino: ...]
"""


def register(registry) -> None:
    registry.register(TranspileSQL())
    registry.register(LookupOracleFunction())
    registry.register(LookupOracleType())
    registry.register(ListTrinoLimitations())
    registry.register(AnalyzeOracleSP())
