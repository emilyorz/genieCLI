# genie/skills/trino_query/SKILL.md: [tree-only: file:line citations required — LLM-written section]
from __future__ import annotations
import: json
import: time
import: urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from genie.core.arg import Arg
from genie.core.registry import BaseSkill
from genie.skills.trino_query.connection import get_active_profile
class: QueryMetrics (__init__.py:24)
  method: def to_json(self) -> dict (__init__.py:42)
  method: def summary_line(self) -> str (__init__.py:59)
class: QueryResult (__init__.py:74)
  method: def to_json(self) -> dict (__init__.py:83)
function: def _human_bytes(b) -> str (__init__.py:100)
function: def _clean_cell(v) (__init__.py:110)
function: def _clean_row(row) -> list (__init__.py:123)
function: def _extract_metrics(stats) -> QueryMetrics (__init__.py:127)
class: TrinoQuerySkill (__init__.py:150)
  method: def run(self, ctx, sql, catalog, schema, limit) -> str (__init__.py:170)
class: TrinoExplainSkill (__init__.py:226)
  method: def run(self, ctx, sql, catalog, schema, type) -> str (__init__.py:241)
class: TrinoSchemaSkill (__init__.py:263)
  method: def run(self, ctx, action, catalog, schema, table) -> str (__init__.py:280)
function: def register(registry) -> None (__init__.py:320)
from __future__ import annotations
import: json
from dataclasses import dataclass, asdict
from pathlib import Path
class: TrinoProfile (connection.py:41)
  method: def connect(self, catalog, schema) (connection.py:50)
  method: def display_name(self) -> str (connection.py:67)
function: def _load_raw() -> dict (connection.py:72)
function: def _save_raw(data) -> None (connection.py:83)
function: def _ensure_default() -> dict (connection.py:90)
function: def list_profiles() -> dict[str, TrinoProfile] (connection.py:113)
function: def get_active_name() -> str (connection.py:122)
function: def get_active_profile() -> TrinoProfile (connection.py:128)
function: def set_active(name) -> bool (connection.py:136)
function: def add_profile(name, profile) -> None (connection.py:146)
function: def remove_profile(name) -> bool (connection.py:153)
function: def status_line() -> str (connection.py:165)
from __future__ import annotations
import: re
from dataclasses import dataclass
class: DetectionFinding (detection_scan.py:16)
function: def _line_from_evidence(evidence) -> int (detection_scan.py:29)
function: def scan_sql(sql) -> list[DetectionFinding] (detection_scan.py:41)
function: def _is_fragment(sql) -> bool (detection_scan.py:157)
function: def _analyze_fragment(sql, analyze_fn) (detection_scan.py:179)
from __future__ import annotations
import: json
import: re
import: time
from genie.core.arg import Arg
from genie.core.registry import BaseSkill
from genie.skills.trino_query import _clean_row, _extract_metrics, _human_bytes, QueryMetrics, QueryResult
from genie.skills.trino_query.connection import get_active_profile
function: def _try_execute(sql, catalog, schema) -> dict (optimize.py:23)
function: def _lint(sql) -> dict (optimize.py:63)
function: def _auto_fix(sql, findings) -> tuple[str, list[str]] (optimize.py:73)
function: def _format_comparison(before, after, changes, lint_before, lint_after) -> str (optimize.py:123)
class: TrinoOptimizeSkill (optimize.py:199)
  method: def run(self, ctx, sql, catalog, schema) -> str (optimize.py:221)
from __future__ import annotations
import: json
import: re
from typing import Any, Optional, Union
function: def _norm_op(name) -> str (plan_signature.py:40)
function: def _extract_table(descriptor) -> Optional[str] (plan_signature.py:50)
function: def _extract_join_type(descriptor) -> Optional[str] (plan_signature.py:59)
function: def _extract_agg_funcs(descriptor) -> tuple (plan_signature.py:69)
function: def _node_sig(node) -> PlanSignature (plan_signature.py:78)
function: def plan_signature(plan) -> Optional[PlanSignature] (plan_signature.py:113)
function: def structural_equivalent(plan_a, plan_b) -> bool (plan_signature.py:138)
from __future__ import annotations
import: re
import: statistics
import: threading
import: time
from pathlib import Path
from typing import Callable, Optional
from genie.core.sql_extraction import extract_sql_from_reply
from genie.skills.mcp_trino.preflight import CandidateTimeoutError, make_candidate_timeout_ms
from genie.skills.trino_query.connection import get_active_profile
from genie.skills.trino_query import QueryMetrics, _extract_metrics
function: def _execute_sql_sync(sql, capture_rows) -> tuple[int, QueryMetrics, list] (research.py:34)
function: def _execute_sql(sql, capture_rows, timeout_ms, label) -> tuple[int, QueryMetrics, list] (research.py:55)
function: def _measure(sql, metric_key, runs, capture_rows, output, label, timeout_ms) -> dict (research.py:127)
function: def _baseline_wall_ms(metrics) -> float (research.py:183)
function: def _normalize_row(row) -> tuple (research.py:202)
function: def _results_equivalent(rows_a, rows_b) -> tuple[bool, str] (research.py:213)
function: def _lint_sql(sql) -> tuple[bool, str] (research.py:245)
function: def _format_static_findings(report) -> str (research.py:257)
function: def _no_data_report() -> str (research.py:268)
function: def _run_no_data_path() -> dict (research.py:365)
function: def _run_plan_cost_loop() -> dict (research.py:507)
function: def _execute_direct_as_dicts(sql) -> list (research.py:728)
function: def _fetch_table_metadata_direct(sql) -> list (research.py:759)
function: def _assemble_direct_directions(original_sql, static_report, explain_runner) (research.py:786)
function: def _run_optimization_loop(provider, model, reasoning, original_sql, metric_key, max_iterations, verify_runs, output, build_prompt) -> dict (research.py:864)
function: def _print_metrics(output, metrics) -> None (research.py:1366)
function: def _iteration_diff(base_sql, candidate_sql) -> str (research.py:1382)
function: def _generate_report(result, metric_key, model, verify_runs) -> str (research.py:1395)
function: def run_trino_research(provider, cfg, model, reasoning, output, build_prompt) -> None (research.py:1474)
from __future__ import annotations
import: logging
from dataclasses import dataclass, field
class: Finding (__init__.py:18)
class: StaticAnalysisReport (__init__.py:27)
  property: def summary(self) -> str (__init__.py:32)
  property: def by_severity(self) -> dict[str, list[Finding]] (__init__.py:39)
  method: def to_dict(self) -> dict (__init__.py:45)
function: def _load_rules() -> list (__init__.py:62)
function: def analyze(sql) -> StaticAnalysisReport (__init__.py:89)
function: def summary_line(report) -> str (__init__.py:137)
from __future__ import annotations
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_JOIN_KEY_COMPUTED
function: def _line_of(sql, needle) -> int (r10_join_key_computed.py:17)
function: def apply(sql, statements) -> list[Finding] (r10_join_key_computed.py:22)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_CARTESIAN_JOIN
function: def _line_of(sql, pattern) -> int (r1_cartesian_join.py:10)
function: def apply(sql, statements) -> list[Finding] (r1_cartesian_join.py:15)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_SELECT_STAR
function: def apply(sql, statements) -> list[Finding] (r2_select_star.py:10)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY
function: def _line_of_distinct(sql) -> int (r3_distinct_after_group_by.py:10)
function: def apply(sql, statements) -> list[Finding] (r3_distinct_after_group_by.py:15)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY
function: def apply(sql, statements) -> list[Finding] (r4_order_by_in_subquery.py:10)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN
function: def apply(sql, statements) -> list[Finding] (r5_subquery_in_select.py:10)
from __future__ import annotations
import: copy
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_PREDICATE_NOT_PUSHED_TO_CTE
function: def apply(sql, statements) -> list[Finding] (r6_predicate_pushdown.py:11)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_NULL_UNSAFE_EQUALS
function: def apply(sql, statements) -> list[Finding] (r7_null_unsafe_equals.py:10)
from __future__ import annotations
import: re
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_REDUNDANT_CAST_CHAIN
function: def apply(sql, statements) -> list[Finding] (r8_redundant_cast.py:10)
from __future__ import annotations
import: re
from dataclasses import dataclass
from genie.skills.trino_query.sql_static import Finding
from genie.skills.trino_query.sql_static.rule_ids import RULE_JOIN_FIRST_FILTER_LATE
class: _BaseRef (r9_join_first_filter_late.py:18)
function: def _select_has_semantic_boundary(select, exp) -> bool (r9_join_first_filter_late.py:23)
function: def _lineage_output_name(projection) -> str | None (r9_join_first_filter_late.py:37)
function: def _single_source(select) (r9_join_first_filter_late.py:45)
function: def _source_alias(source) -> str (r9_join_first_filter_late.py:53)
function: def _resolve_source_select(source, cte_map, exp) (r9_join_first_filter_late.py:58)
function: def _resolve_joined_producer_lineage(select, exp) -> dict[str, _BaseRef] | None (r9_join_first_filter_late.py:66)
function: def _resolve_producer_lineage(select, cte_map, exp, remaining_hops) -> dict[str, _BaseRef] | None (r9_join_first_filter_late.py:111)
function: def _flatten_and(expr, exp) -> list (r9_join_first_filter_late.py:150)
function: def _predicate_base_refs(predicate, consumer_source_alias, producer_lineage, exp) -> list[_BaseRef] | None (r9_join_first_filter_late.py:156)
function: def _token_signature(sql, tokenizer) -> tuple[str, ...] (r9_join_first_filter_late.py:172)
function: def _find_where_line(sql, where_expr) -> int | None (r9_join_first_filter_late.py:176)
function: def apply(sql, statements) -> list[Finding] (r9_join_first_filter_late.py:233)
