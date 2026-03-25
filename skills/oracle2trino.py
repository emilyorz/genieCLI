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

import re
from pathlib import Path

import yaml

from .base import Arg, BaseSkill

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_SQL_DISPLAY = 3000  # M3: unified truncation limit across all tools

# ── YAML loader (lazy, cached) ────────────────────────────────────────────────

_DB: dict | None = None


def _load_db() -> dict:
    global _DB
    if _DB is None:
        yaml_path = Path(__file__).parent.parent / "data" / "oracle_trino_functions.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            _DB = yaml.safe_load(f)
    return _DB


def _truncate(text: str, limit: int = MAX_SQL_DISPLAY) -> str:
    if len(text) > limit:
        return text[:limit] + "..."
    return text


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

        # C1: Use IGNORE level — we don't need parse warnings,
        # and this avoids the thread-unsafe stderr redirect entirely.
        results = sqlglot.transpile(
            sql,
            read="oracle",
            write="trino",
            error_level=sge.ErrorLevel.IGNORE,
        )
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
        lines.append(_truncate(sql))
        lines.append("")
        lines.append("--- Transpiled Output ---")
        lines.append(_truncate(transpiled))
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
        funcs = db.get("functions", [])

        # H1: Exact match first, fallback to substring only if no exact match
        exact = [e for e in funcs if needle == str(e.get("oracle", "")).upper()]
        if exact:
            results = exact
        else:
            results = [e for e in funcs if needle in str(e.get("oracle", "")).upper()]

        if not results:
            return (
                f"No mapping found for '{oracle_name}'. "
                "It may be a PL/SQL construct with no Trino equivalent — consider moving logic to Python."
            )

        lines: list[str] = []
        for r in results[:5]:  # allow up to 5 for substring fallback
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
            transpile_section += "✅ sqlglot converted some constructs.\n\n"
            transpile_section += f"Transpiled output:\n{_truncate(transpiled)}\n"
        else:
            transpile_section += "⚠️  sqlglot made no changes (likely pure PL/SQL or unsupported syntax).\n"

        if transpile_warnings:
            transpile_section += "\nRemaining issues after sqlglot:\n"
            for w in transpile_warnings:
                transpile_section += f"  {w}\n"

        # ── Step 2: PL/SQL construct detection ────────────────────────────────
        # H2: Use regex word boundaries and contextual patterns to reduce false positives
        upper_sql = sql.upper()
        flags: list[str] = []

        # Patterns: (regex_pattern, keyword_label, message)
        # Using \b word boundaries and context-aware patterns
        plsql_patterns: list[tuple[str, str, str]] = [
            (r'(?:^|\s)BEGIN\b',               "BEGIN",            "PL/SQL block — wrap queries in Python, remove procedural shell"),
            (r'(?:^|\s)DECLARE\b',             "DECLARE",          "Variable declarations — replace with CTEs or Python variables"),
            (r'\bCURSOR\b',                    "CURSOR",           "Cursor — rewrite as Python loop + multiple Trino queries"),
            (r'\bFOR\s+\w+\s+IN\b',            "FOR...IN",         "FOR loop — move iteration to Python/Airflow orchestration"),
            (r'(?:^|\s)LOOP\b|\bEND\s+LOOP\b', "LOOP",            "LOOP — move to Python orchestration"),
            (r'\bEXCEPTION\b',                 "EXCEPTION",        "Exception handling — move to Python try/except"),
            (r'\bEXECUTE\s+IMMEDIATE\b',       "EXECUTE IMMEDIATE","Dynamic SQL — move to Python f-string + Trino execute"),
            (r'\bDBMS_\w+',                    "DBMS_*",           "Oracle DBMS package — no Trino equivalent, move to Python"),
            (r'\bCONNECT\s+BY\b',             "CONNECT BY",       "Hierarchical query — rewrite as WITH RECURSIVE CTE (sqlglot may be wrong)"),
            (r'\bROWNUM\b',                    "ROWNUM",           "ROWNUM — rewrite as ROW_NUMBER() subquery or FETCH FIRST n ROWS"),
            (r'\bMERGE\s+INTO\b',             "MERGE INTO",       f"MERGE — Iceberg only (Trino 420+), verify connector={connector}"),
            (r'\(\+\)',                        "(+)",              "Oracle outer join (+) — rewrite as ANSI LEFT/RIGHT JOIN"),
            (r'(?:^|\s)PIVOT\b',              "PIVOT",            "PIVOT — rewrite as CASE-WHEN aggregation"),
            (r'(?:^|\s)UNPIVOT\b',            "UNPIVOT",          "UNPIVOT — rewrite as CROSS JOIN UNNEST"),
            (r'\bLISTAGG\b',                  "LISTAGG",          "LISTAGG — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            (r'\bWM_CONCAT\b',               "WM_CONCAT",        "WM_CONCAT (deprecated) — rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')"),
            (r'\bMONTHS_BETWEEN\b',          "MONTHS_BETWEEN",   "MONTHS_BETWEEN — rewrite as DATE_DIFF('month', d2, d1)"),
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
{_truncate(sql)}

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
