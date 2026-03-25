"""
oracle2trino skill — Oracle SP to Trino SQL migration assistant.

Tools:
  - lookup_oracle_function: query the local YAML mapping table
  - list_trino_limitations: return all known Trino hard limits
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .base import Arg, BaseSkill

# ── YAML loader (lazy, cached) ────────────────────────────────────────────────

_DB: dict | None = None


def _load_db() -> dict:
    global _DB
    if _DB is None:
        yaml_path = Path(__file__).parent.parent / "data" / "oracle_trino_functions.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            _DB = yaml.safe_load(f)
    return _DB


# ── Skills ────────────────────────────────────────────────────────────────────


class LookupOracleFunction(BaseSkill):
    name = "lookup_oracle_function"
    description = (
        "Look up how to convert an Oracle SQL function or syntax to Trino equivalent. "
        "Returns the Trino equivalent, an example, and migration notes."
    )
    group = "oracle2trino"
    args = [
        Arg(
            name="oracle_name",
            type="str",
            description="Oracle function or syntax name (e.g. NVL, SYSDATE, CONNECT BY, ROWNUM)",
            required=True,
        ),
    ]

    def run(self, oracle_name: str = "") -> str:
        db = _load_db()
        needle = oracle_name.strip().upper()
        results: list[dict] = []

        for entry in db.get("functions", []):
            if needle in str(entry.get("oracle", "")).upper():
                results.append(entry)

        if not results:
            return (
                f"No mapping found for '{oracle_name}'. "
                "It may be a PL/SQL construct with no Trino equivalent — consider moving logic to Python."
            )

        lines: list[str] = []
        for r in results[:3]:  # cap at 3 matches
            lines.append(f"Oracle: {r['oracle']}")
            trino_eq = r.get("trino") or "No direct equivalent"
            lines.append(f"Trino:  {trino_eq}")
            if r.get("example"):
                lines.append(f"Example: {r['example']}")
            if r.get("notes"):
                lines.append(f"Notes:  {r['notes']}")
            lines.append("")
        return "\n".join(lines).strip()


class LookupOracleType(BaseSkill):
    name = "lookup_oracle_type"
    description = (
        "Look up how to convert an Oracle data type to Trino equivalent. "
        "Returns the Trino type and any important migration notes."
    )
    group = "oracle2trino"
    args = [
        Arg(
            name="oracle_type",
            type="str",
            description="Oracle data type name (e.g. VARCHAR2, NUMBER, DATE, CLOB)",
            required=True,
        ),
    ]

    def run(self, oracle_type: str = "") -> str:
        db = _load_db()
        needle = oracle_type.strip().upper()

        for entry in db.get("types", []):
            if needle == str(entry.get("oracle", "")).upper():
                trino_t = entry.get("trino", "Unknown")
                notes = entry.get("notes", "")
                result = f"Oracle: {entry['oracle']}\nTrino:  {trino_t}"
                if notes:
                    result += f"\nNotes:  {notes}"
                return result

        return f"No type mapping found for '{oracle_type}'."


class ListTrinoLimitations(BaseSkill):
    name = "list_trino_limitations"
    description = (
        "List all known Trino hard limits that affect Oracle SP migration. "
        "Useful when assessing whether a feature can be converted."
    )
    group = "oracle2trino"
    args = []

    def run(self) -> str:
        db = _load_db()
        limits = db.get("trino_limitations", [])
        if not limits:
            return "No limitations data found."
        lines = ["Trino Hard Limits (cannot be directly migrated from Oracle):"]
        for i, lim in enumerate(limits, 1):
            lines.append(f"  {i}. {lim}")
        return "\n".join(lines)


class AnalyzeOracleSP(BaseSkill):
    name = "analyze_oracle_sp"
    description = (
        "Analyze an Oracle stored procedure or SQL snippet and return a migration plan. "
        "Identifies which parts can be converted to Trino SQL, which need orchestration rewrite, "
        "and which require manual redesign. Outputs structured analysis with confidence score."
    )
    group = "oracle2trino"
    args = [
        Arg(
            name="sql",
            type="str",
            description="Oracle PL/SQL or SQL text to analyze",
            required=True,
        ),
        Arg(
            name="connector",
            type="str",
            description="Target Trino connector type (hive / iceberg / delta / generic)",
            required=False,
            default="iceberg",
            choices=["hive", "iceberg", "delta", "generic"],
        ),
    ]

    def run(self, sql: str = "", connector: str = "iceberg") -> str:
        """
        This skill provides context to the AI — the actual analysis is done by the LLM.
        We return structured hints about what to look for, plus the SQL for the AI to process.
        """
        db = _load_db()
        limits = db.get("trino_limitations", [])
        limits_text = "\n".join(f"- {l}" for l in limits)

        # Detect obvious PL/SQL constructs
        upper_sql = sql.upper()
        flags: list[str] = []

        plsql_keywords = [
            ("BEGIN", "PL/SQL block detected"),
            ("DECLARE", "Variable declarations detected"),
            ("CURSOR", "Cursor usage detected — rewrite as Python loop + Trino queries"),
            ("FOR ", "FOR loop detected — may need orchestration rewrite"),
            ("LOOP", "LOOP construct detected — rewrite as orchestration logic"),
            ("EXCEPTION", "Exception handling detected — move to Python try/except"),
            ("EXECUTE IMMEDIATE", "Dynamic SQL detected — move to Python layer"),
            ("DBMS_", "Oracle DBMS package call detected — no Trino equivalent"),
            ("CONNECT BY", "Hierarchical query detected — rewrite as recursive CTE"),
            ("ROWNUM", "ROWNUM usage — rewrite as ROW_NUMBER() window function"),
            ("MERGE INTO", f"MERGE detected — only supported on Iceberg with Trino 420+ (target: {connector})"),
            ("(+)", "Oracle outer join syntax (+) — rewrite as ANSI LEFT/RIGHT JOIN"),
            ("PIVOT", "PIVOT detected — rewrite as manual CASE-WHEN aggregation"),
            ("UNPIVOT", "UNPIVOT detected — rewrite as CROSS JOIN UNNEST"),
            ("LISTAGG", "LISTAGG — convert to ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("WM_CONCAT", "WM_CONCAT (deprecated) — convert to ARRAY_JOIN(ARRAY_AGG(...), ',')"),
        ]

        for keyword, message in plsql_keywords:
            if keyword in upper_sql:
                flags.append(f"⚠️  {message}")

        flags_text = "\n".join(flags) if flags else "✅ No obvious PL/SQL constructs detected — likely pure SQL."

        connector_notes = {
            "hive": "Hive connector: no DML (INSERT only via CTAS), no MERGE, no DELETE/UPDATE.",
            "iceberg": "Iceberg connector: supports INSERT/UPDATE/DELETE, limited MERGE (Trino 420+).",
            "delta": "Delta connector: supports INSERT, limited UPDATE/DELETE, no MERGE.",
            "generic": "Generic connector: assume read-only; no DML support.",
        }.get(connector, "")

        return f"""=== Oracle SP Analysis Context ===

Target connector: {connector}
{connector_notes}

--- Detected Constructs ---
{flags_text}

--- Trino Hard Limits (reference) ---
{limits_text}

--- SQL to Analyze ---
{sql[:3000]}{"..." if len(sql) > 3000 else ""}

--- Instructions for AI ---
Using the above context, provide a structured migration report:

1. ANALYSIS
   - Estimated convertibility: X% (pure SQL queries vs. procedural logic ratio)
   - Breakdown of detected constructs and complexity

2. CONVERTED SQL
   - Trino-compatible SQL for the convertible parts
   - Annotate each change with a comment like -- [Oracle→Trino: NVL→COALESCE]

3. MIGRATION NOTES
   - List of constructs that need orchestration rewrite (with suggested approach)
   - List of constructs that need manual redesign
   - Connector-specific warnings (based on {connector})
   - Questions to clarify with the original SP owner
"""
