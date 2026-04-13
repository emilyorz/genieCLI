"""Oracle to Trino SQL migration skill package."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from genie.core.arg import Arg
from genie.core.registry import BaseSkill
from .models import ConversionResult, UnsupportedConstruct
from genie.core.sql_patterns import ORACLE_CONSTRUCTS, compute_confidence
from genie.core.sql_utils import strip_comments_and_strings as _strip_comments_and_strings

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


def _sqlglot_transpile(sql: str) -> tuple[str, list[str], bool]:
    """Transpile Oracle SQL to Trino via sqlglot.

    Returns:
        (transpiled_sql, error_messages, success)
        success=False means sqlglot is missing or raised an exception.
    """
    try:
        import sqlglot
        import sqlglot.errors as sge

        results = sqlglot.transpile(
            sql, read="oracle", write="trino",
            error_level=sge.ErrorLevel.IGNORE,
        )
        transpiled = results[0] if results else sql
        return transpiled, [], True
    except ImportError:
        return sql, ["sqlglot not installed — run: pip install sqlglot"], False
    except Exception as exc:
        return sql, [f"sqlglot transpile error: {exc}"], False


def _detect_unsupported(sql: str) -> list[UnsupportedConstruct]:
    """Scan *sql* against the shared ORACLE_CONSTRUCTS catalog.

    Returns one UnsupportedConstruct per matched construct (no duplicates).
    Comments and string literals are stripped before matching so that Oracle
    keywords inside them are not false-positively flagged.
    """
    stripped = _strip_comments_and_strings(sql)
    seen: set[str] = set()
    result: list[UnsupportedConstruct] = []
    for entry in ORACLE_CONSTRUCTS:
        name = entry["construct"]
        if name in seen:
            continue
        if re.search(entry["pattern"], stripped, re.IGNORECASE):
            seen.add(name)
            result.append(UnsupportedConstruct(
                construct=name,
                severity=entry["severity"],
                message=entry["message"],
                suggestion=entry["suggestion"],
            ))
    return result


class TranspileSQL(BaseSkill):
    name = "transpile_sql"
    description = (
        "Run sqlglot to mechanically transpile Oracle SQL to Trino SQL. "
        "Handles ~50% of common syntax differences automatically. "
        "Returns a JSON ConversionResult with converted_sql, unsupported constructs, "
        "warnings, confidence score, and manual_fix_notes."
    )
    group = "oracle2trino"
    args = [Arg(name="sql", type="str", description="Oracle SQL statement(s) to transpile", required=True)]

    def run(self, sql: str = "") -> str:
        transpiled, errors, success = _sqlglot_transpile(sql)

        # Detect Oracle constructs in the ORIGINAL SQL. Checking the input (not
        # the transpiled output) ensures we don't miss constructs that sqlglot
        # "handles" with an incorrect or lossy conversion (e.g. BEGIN...END → START
        # TRANSACTION). The converted_sql field shows exactly what sqlglot produced.
        unsupported = _detect_unsupported(sql)

        if not success:
            result = ConversionResult(
                converted_sql=transpiled,
                unsupported=unsupported,
                warnings=errors,
                confidence=0.0,
                manual_fix_notes=[
                    "sqlglot transpilation failed — manual conversion required.",
                    *errors,
                ],
            )
        else:
            result = ConversionResult(
                converted_sql=transpiled,
                unsupported=unsupported,
                warnings=[],
                confidence=compute_confidence(unsupported),
                manual_fix_notes=[],
            )

        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


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
        "Runs sqlglot transpile, detects Oracle and PL/SQL constructs, and returns "
        "a JSON ConversionResult with structured guidance."
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
        transpiled, errors, success = _sqlglot_transpile(sql)

        # Detect all Oracle constructs in the ORIGINAL SQL (before transpilation)
        unsupported = _detect_unsupported(sql)

        # Connector-specific notes go into warnings
        connector_note = {
            "hive":    "Hive: read-heavy, no UPDATE/DELETE/MERGE, INSERT only via CTAS.",
            "iceberg": "Iceberg: INSERT/UPDATE/DELETE supported, limited MERGE (Trino 420+).",
            "delta":   "Delta: INSERT supported, limited UPDATE/DELETE, no MERGE.",
            "generic": "Generic: assume read-only, no DML.",
        }.get(connector, "")

        warnings: list[str] = []
        if connector_note:
            warnings.append(f"Connector ({connector}): {connector_note}")
        if errors:
            warnings.extend(errors)

        # Build manual_fix_notes for PL/SQL constructs and connector-relevant items
        manual_fix_notes: list[str] = []
        plsql_entry_map = {
            e["construct"]: e for e in ORACLE_CONSTRUCTS if e["plsql"]
        }
        for item in unsupported:
            if item.construct in plsql_entry_map:
                manual_fix_notes.append(
                    f"[{item.construct}] {item.suggestion}"
                )

        if manual_fix_notes:
            manual_fix_notes.insert(
                0,
                "Start from the sqlglot transpiled output. Fix each item below, "
                "annotating every change: -- [Oracle→Trino: ...]",
            )

        if not success:
            confidence = 0.0
            manual_fix_notes.append("sqlglot transpilation failed — manual conversion required.")
        else:
            confidence = compute_confidence(unsupported)

        result = ConversionResult(
            converted_sql=transpiled,
            unsupported=unsupported,
            warnings=warnings,
            confidence=confidence,
            manual_fix_notes=manual_fix_notes,
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


class LintTrinoSQL(BaseSkill):
    name = "lint_trino_sql"
    description = (
        "Static SQL linter for Trino queries. "
        "Detects Oracle residuals (NVL, DECODE, ROWNUM, SYSDATE, (+) joins), "
        "SELECT *, implicit cross joins, leading wildcards, COUNT(DISTINCT), "
        "correlated subqueries, and missing partition filters. "
        "Returns structured findings with severity, rule, score, and fix suggestions."
    )
    group = "oracle2trino"
    args = [
        Arg(
            name="sql",
            type="str",
            description="Trino SQL statement(s) to lint (multiple statements separated by ;)",
            required=True,
        )
    ]

    def run(self, sql: str = "") -> str:
        from genie.core.lint_analyzer import analyze
        result = analyze(sql)
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def register(registry) -> None:
    registry.register(TranspileSQL())
    registry.register(LookupOracleFunction())
    registry.register(LookupOracleType())
    registry.register(ListTrinoLimitations())
    registry.register(AnalyzeOracleSP())
    registry.register(LintTrinoSQL())
