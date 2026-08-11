"""D1 analysis-coverage evaluation package (static SCAN metrics)."""
from genie.skills.mcp_trino.d1_eval.analyze import analyze_sql
from genie.skills.mcp_trino.d1_eval.oracle_match import Finding, MatchResult, match_findings
from genie.skills.mcp_trino.d1_eval.taxonomy import FindingCategory, normalize_object

__all__ = [
    "FindingCategory",
    "normalize_object",
    "Finding",
    "MatchResult",
    "match_findings",
    "analyze_sql",
]
