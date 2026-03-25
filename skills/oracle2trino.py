"""
oracle2trino skill — Oracle SP to Trino SQL migration assistant.

Tools:
  - transpile_sql         : sqlglot 機械轉換（第一 pass，快速省力）
  - lookup_oracle_function: YAML 函數對照查表
  - lookup_oracle_type    : YAML 型別對照查表
  - list_trino_limitations: Trino 硬限制清單
  - analyze_oracle_sp     : 完整 SP 分析（auto-transpile + construct detection + AI instructions）
"""
from __future__ import annotations

from pathlib import Path

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


# ── sqlglot helper ────────────────────────────────────────────────────────────

def _sqlglot_transpile(sql: str) -> tuple[str, list[str]]:
    """
    Run sqlglot Oracle→Trino transpile.
    Returns (transpiled_sql, list_of_warnings).
    Falls back gracefully if sqlglot not installed.
    """
    try:
        import sqlglot
        import sqlglot.errors as sge

        warnings: list[str] = []
        errors_caught: list[str] = []

        # Collect parse/transpile errors as warnings (don't crash, suppress stderr noise)
        import io, sys
        _stderr_capture = io.StringIO()
        _old_stderr = sys.stderr
        sys.stderr = _stderr_capture
        try:
            results = sqlglot.transpile(
                sql,
                read="oracle",
                write="trino",
                error_level=sge.ErrorLevel.WARN,
            )
        finally:
            sys.stderr = _old_stderr
        transpiled = results[0] if results else sql

        # Detect constructs sqlglot leaves unchanged (known gaps)
        known_gaps = [
            ("ROWNUM",          "ROWNUM not converted — rewrite as ROW_NUMBER() subquery or FETCH FIRST n ROWS"),
            ("LISTAGG",         "LISTAGG not converted — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("MONTHS_BETWEEN",  "MONTHS_BETWEEN not converted — rewrite as DATE_DIFF('month', d2, d1)"),
            ("WM_CONCAT",       "WM_CONCAT not converted — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("CONNECT BY",      "CONNECT BY may not be fully converted — verify recursive CTE output"),
            ("PIVOT",           "PIVOT may be dropped or incorrect — rewrite as CASE-WHEN aggregation"),
            ("UNPIVOT",         "UNPIVOT not converted — rewrite as CROSS JOIN UNNEST"),
            ("EXECUTE IMMEDIATE","EXECUTE IMMEDIATE not converted — move to Python layer"),
        ]
        upper_out = transpiled.upper()
        for keyword, msg in known_gaps:
            if keyword in upper_out:
                warnings.append(f"⚠️  {msg}")

        return transpiled, warnings

    except ImportError:
        return sql, ["sqlglot not installed — run: pip install sqlglot"]
    except Exception as e:
        return sql, [f"sqlglot transpile error: {e}"]


# ── Skills ────────────────────────────────────────────────────────────────────


class TranspileSQL(BaseSkill):
    name = "transpile_sql"
    description = (
        "Run sqlglot to mechanically transpile Oracle SQL to Trino SQL. "
        "Handles ~50% of common syntax differences automatically (DECODE→CASE, TO_DATE, TRUNC, etc.). "
        "Returns transpiled SQL + warnings for constructs that need manual attention. "
        "Always run this FIRST before asking AI to review or fix remaining issues."
    )
    group = "oracle2trino"
    args = [
        Arg(
            name="sql",
            type="str",
            description="Oracle SQL statement(s) to transpile",
            required=True,
        ),
    ]

    def run(self, sql: str = "") -> str:
        transpiled, warnings = _sqlglot_transpile(sql)

        lines: list[str] = ["=== sqlglot Oracle→Trino Transpile ===", ""]
        lines.append("--- Input ---")
        lines.append(sql[:2000] + ("..." if len(sql) > 2000 else ""))
        lines.append("")
        lines.append("--- Transpiled Output ---")
        lines.append(transpiled[:2000] + ("..." if len(transpiled) > 2000 else ""))
        lines.append("")

        if warnings:
            lines.append("--- Warnings (needs AI review) ---")
            for w in warnings:
                lines.append(w)
        else:
            lines.append("✅ No known gaps detected in transpiled output.")

        lines.append("")
        lines.append(
            "Next step: review the transpiled output above. "
            "Fix any warnings using lookup_oracle_function / lookup_oracle_type, "
            "then produce the final Trino SQL with inline change annotations."
        )
        return "\n".join(lines)


class LookupOracleFunction(BaseSkill):
    name = "lookup_oracle_function"
    description = (
        "Look up how to convert an Oracle SQL function or syntax to Trino equivalent. "
        "Returns the Trino equivalent, an example, and migration notes. "
        "Use this for constructs that sqlglot left unchanged or converted incorrectly."
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
        for r in results[:3]:
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
        "Full analysis of an Oracle stored procedure or SQL for Trino migration. "
        "Automatically runs sqlglot transpile as first pass, detects PL/SQL constructs, "
        "and provides structured context for the AI to complete the migration. "
        "Use this as the entry point for any Oracle→Trino migration task."
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
        db = _load_db()
        limits = db.get("trino_limitations", [])
        limits_text = "\n".join(f"- {lim}" for lim in limits)

        # ── Step 1: sqlglot auto-transpile ────────────────────────────────────
        transpiled, transpile_warnings = _sqlglot_transpile(sql)
        changed = transpiled.strip() != sql.strip()

        transpile_section = "--- Step 1: sqlglot Auto-Transpile ---\n"
        if changed:
            transpile_section += f"✅ sqlglot converted some constructs.\n\n"
            transpile_section += f"Transpiled output:\n{transpiled[:2000]}{'...' if len(transpiled) > 2000 else ''}\n"
        else:
            transpile_section += "⚠️  sqlglot made no changes (likely pure PL/SQL or unsupported syntax).\n"

        if transpile_warnings:
            transpile_section += "\nRemaining issues after sqlglot:\n"
            for w in transpile_warnings:
                transpile_section += f"  {w}\n"

        # ── Step 2: PL/SQL construct detection ────────────────────────────────
        upper_sql = sql.upper()
        flags: list[str] = []

        plsql_keywords = [
            ("BEGIN",            "PL/SQL block — wrap queries in Python, remove procedural shell"),
            ("DECLARE",          "Variable declarations — replace with CTEs or Python variables"),
            ("CURSOR",           "Cursor — rewrite as Python loop + multiple Trino queries"),
            ("FOR ",             "FOR loop — move iteration to Python/Airflow orchestration"),
            ("LOOP",             "LOOP — move to Python orchestration"),
            ("EXCEPTION",        "Exception handling — move to Python try/except"),
            ("EXECUTE IMMEDIATE","Dynamic SQL — move to Python f-string + Trino execute"),
            ("DBMS_",            "Oracle DBMS package — no Trino equivalent, move to Python"),
            ("CONNECT BY",       "Hierarchical query — rewrite as WITH RECURSIVE CTE (sqlglot may be wrong)"),
            ("ROWNUM",           "ROWNUM — rewrite as ROW_NUMBER() subquery or FETCH FIRST n ROWS"),
            ("MERGE INTO",       f"MERGE — Iceberg only (Trino 420+), verify connector={connector}"),
            ("(+)",              "Oracle outer join (+) — rewrite as ANSI LEFT/RIGHT JOIN"),
            ("PIVOT",            "PIVOT — rewrite as CASE-WHEN aggregation"),
            ("UNPIVOT",          "UNPIVOT — rewrite as CROSS JOIN UNNEST"),
            ("LISTAGG",          "LISTAGG — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("WM_CONCAT",        "WM_CONCAT (deprecated) — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            ("MONTHS_BETWEEN",   "MONTHS_BETWEEN — rewrite as DATE_DIFF('month', d2, d1)"),
        ]

        for keyword, message in plsql_keywords:
            if keyword in upper_sql:
                flags.append(f"⚠️  [{keyword}] {message}")

        flags_text = "\n".join(flags) if flags else "✅ No PL/SQL constructs detected — likely pure SQL."

        connector_notes = {
            "hive":    "Hive: read-heavy, no UPDATE/DELETE/MERGE, INSERT only via CTAS.",
            "iceberg": "Iceberg: INSERT/UPDATE/DELETE supported, limited MERGE (Trino 420+).",
            "delta":   "Delta: INSERT supported, limited UPDATE/DELETE, no MERGE.",
            "generic": "Generic: assume read-only, no DML.",
        }.get(connector, "")

        # ── Assemble output ───────────────────────────────────────────────────
        return f"""=== Oracle SP Migration Analysis ===

Target connector : {connector}
Connector notes  : {connector_notes}

{transpile_section}
--- Step 2: Detected Constructs ---
{flags_text}

--- Trino Hard Limits (reference) ---
{limits_text}

--- Original SQL ---
{sql[:3000]}{"..." if len(sql) > 3000 else ""}

--- Instructions for AI ---
Using the transpiled output from Step 1 as your starting point (not the original Oracle SQL),
complete the migration by fixing the remaining warnings and constructs:

1. ANALYSIS
   - Estimated convertibility: X% (pure SQL ratio vs. procedural logic)
   - Which constructs were auto-converted by sqlglot ✅
   - Which constructs still need attention ⚠️
   - Which constructs require full manual redesign ❌

2. FINAL TRINO SQL
   - Start from the sqlglot transpiled output
   - Fix each remaining warning manually
   - Annotate every change: -- [Oracle→Trino: ROWNUM→ROW_NUMBER()]
   - For PL/SQL blocks: extract only the SELECT/INSERT queries and mark the rest as [ORCHESTRATION NEEDED]

3. MIGRATION NOTES
   - Orchestration rewrite items (what Python/Airflow needs to handle)
   - Manual redesign items (architecture-level changes)
   - Connector-specific risks for {connector}
   - Open questions for the original SP owner
"""
