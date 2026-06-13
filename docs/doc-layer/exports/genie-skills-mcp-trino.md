# genie/skills/mcp_trino/SKILL.md: [tree-only: file:line citations required — LLM-written section]
from __future__ import annotations
import: json
import: os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from genie.core.arg import Arg
from genie.core.registry import BaseSkill
function: def __getattr__(name) -> Any (__init__.py:20)
class: McpTrinoSkill (__init__.py:27)
  method: def __init__(self, tool_def, client) -> None (__init__.py:33)
  method: def run(self, **kwargs) -> str (__init__.py:40)
  method: def run_tool(self, name, args, ctx) -> str (__init__.py:43)
class: McpTrinoStatusSkill (__init__.py:55)
  method: def __init__(self, client) -> None (__init__.py:64)
  method: def run(self, **kwargs) -> str (__init__.py:67)
function: def _build_args(schema) -> list[Arg] (__init__.py:87)
function: def register(registry) -> None (__init__.py:109)
from __future__ import annotations
import: json
import: os
import: uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import: requests
class: McpConfig (client.py:28)
  method: def endpoint(self) -> str (client.py:34)
function: def load_mcp_config() -> McpConfig (client.py:39)
function: def save_mcp_config(config) -> None (client.py:93)
class: McpClient (client.py:114)
  method: def __init__(self, config) -> None (client.py:117)
  method: def _next_id(self) -> int (client.py:128)
  method: def _post(self, method, params) -> Any (client.py:132)
  method: def _parse_sse_response(self, text) -> Any (client.py:172)
  method: def _ensure_initialized(self) -> None (client.py:190)
  method: def server_info(self) -> dict (client.py:211)
  method: def list_tools(self) -> list[dict] (client.py:221)
  method: def call_tool(self, name, arguments) -> str (client.py:229)
class: McpError (client.py:252)
  method: def __init__(self, code, message) -> None (client.py:255)
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional
class: CostReading (cost_reader.py:25)
function: def read_cost(sql, explain_runner) -> CostReading (cost_reader.py:34)
from __future__ import annotations
import: math
import: os
import: re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from genie.skills.trino_query.sql_static.rule_ids import RULE_CARTESIAN_JOIN, RULE_JOIN_FIRST_FILTER_LATE, RULE_JOIN_KEY_COMPUTED, RULE_NULL_UNSAFE_EQUALS, RULE_PREDICATE_NOT_PUSHED_TO_CTE, RULE_REDUNDANT_CAST_CHAIN, RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY, RULE_SELECT_STAR, RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN, RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY
function: def _resolve_memory_pressure_fraction() -> float (pre_execution_diagnosis.py:67)
function: def _memory_pressure_threshold(peak_memory_limit_bytes) -> int (pre_execution_diagnosis.py:84)
class: OptimizationDirection (pre_execution_diagnosis.py:119)
function: def _sort_key(d) -> tuple[int, int, str, str] (pre_execution_diagnosis.py:134)
function: def _static_contributor(static_report) -> list[OptimizationDirection] (pre_execution_diagnosis.py:162)
function: def _name_tokens(name) -> set[str] (pre_execution_diagnosis.py:224)
function: def _looks_like_raw_table(table_name) -> bool (pre_execution_diagnosis.py:228)
function: def _table_name(table) -> str (pre_execution_diagnosis.py:236)
function: def _sql_shape_contributor(sql) -> list[OptimizationDirection] (pre_execution_diagnosis.py:245)
function: def _max_non_leaf_output_bytes(node) -> int | None (pre_execution_diagnosis.py:336)
function: def _encoding_a_distribution(node_name) -> str | None (pre_execution_diagnosis.py:376)
function: def _encoding_b_distribution(node) -> str | None (pre_execution_diagnosis.py:395)
function: def _remoteexchange_child_distribution(node) -> str | None (pre_execution_diagnosis.py:422)
function: def _resolve_node_distribution(node, parent_dist) -> str (pre_execution_diagnosis.py:443)
function: def _extract_side_estimates(children, idx) -> tuple[float | None, float | None] (pre_execution_diagnosis.py:453)
function: def _extract_join_facts(raw_plan_json) -> list[dict] (pre_execution_diagnosis.py:497)
function: def _diagnose_join_facts(join_facts) -> list[OptimizationDirection] (pre_execution_diagnosis.py:547)
function: def _join_diagnosis_contributor(explain_cost) -> list[OptimizationDirection] (pre_execution_diagnosis.py:626)
function: def _explain_cost_contributor(explain_cost) -> list[OptimizationDirection] (pre_execution_diagnosis.py:650)
function: def _partition_spec(properties) -> str (pre_execution_diagnosis.py:719)
function: def _metadata_contributor(table_metadata) -> list[OptimizationDirection] (pre_execution_diagnosis.py:734)
function: def _memory_contributor(peak_memory_bytes) -> list[OptimizationDirection] (pre_execution_diagnosis.py:786)
function: def format_directions_for_prompt(directions) -> str (pre_execution_diagnosis.py:821)
function: def format_directions_report(directions) -> str (pre_execution_diagnosis.py:856)
class: DirectionOutcome (pre_execution_diagnosis.py:962)
function: def attribute_directions(directions, baseline_metrics, best_metrics) -> list[DirectionOutcome] (pre_execution_diagnosis.py:980)
function: def format_attribution_report(outcomes) -> str (pre_execution_diagnosis.py:1027)
function: def pre_execution_diagnosis(sql) -> list[OptimizationDirection] (pre_execution_diagnosis.py:1056)
from __future__ import annotations
import: enum
import: json
import: re
from dataclasses import dataclass
from typing import Optional
class: PreflightBudget (preflight.py:32)
class: PreflightReport (preflight.py:39)
function: def check_read_only(sql) -> tuple[bool, str] (preflight.py:47)
function: def estimate_from_explain(explain_result) -> tuple[Optional[int], Optional[int]] (preflight.py:79)
function: def plan_cost(sql, explain_runner) -> tuple[Optional[int], Optional[int], Optional[object]] (preflight.py:125)
function: def _combine_cost(rows, bytes_) -> Optional[int] (preflight.py:162)
function: def run_preflight(sql, explain_runner, budget) -> PreflightReport (preflight.py:184)
class: CandidateTimeoutError (preflight.py:249)
  method: def __init__(self, timeout_ms, label) -> None (preflight.py:252)
class: LongQueryAbort (preflight.py:261)
  method: def __init__(self, message, baseline_s, predicted_total_s, report_markdown) (preflight.py:268)
class: NoDataDetected (preflight.py:283)
  method: def __init__(self, reason, result) (preflight.py:291)
class: LongQueryGateResult (preflight.py:298)
class: PreflightRoute (preflight.py:310)
class: PreflightDecision (preflight.py:325)
function: def check_long_query_gate(baseline_wall_ms, max_iterations) -> LongQueryGateResult (preflight.py:337)
class: _SafeOutput (preflight.py:385)
  method: def __init__(self, output) (preflight.py:393)
  method: def print(self, *a, **kw) (preflight.py:396)
  method: def progress(self, *a, **kw) (preflight.py:400)
  method: def error(self, *a, **kw) (preflight.py:404)
from typing import NamedTuple
class: _PlanCostCoreResult (preflight.py:412)
function: def _plan_cost_loop_core() -> '_PlanCostCoreResult' (preflight.py:424)
function: def make_query_max_run_time_sql(baseline_wall_ms) -> str (preflight.py:676)
function: def make_candidate_timeout_ms(baseline_wall_ms) -> int (preflight.py:687)
function: def apply_safe_limit(sql, limit) -> str (preflight.py:701)
function: def detect_no_data_reason() -> Optional[str] (preflight.py:731)
function: def build_preflight_decision() -> 'PreflightDecision' (preflight.py:762)
from __future__ import annotations
import: json
import: math
import: re
import: statistics
import: time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
import: requests
from rich.markup import escape
from genie.core.sql_extraction import extract_sql_from_reply
from genie.skills.mcp_trino.client import McpClient, McpConfig, McpError, load_mcp_config
from genie.skills.mcp_trino.preflight import CandidateTimeoutError, make_candidate_timeout_ms
from genie.skills.mcp_trino.write_analysis import classify_write_operation, run_write_analysis_only
import: sqlglot
class: RunMetrics (research.py:42)
  method: def summary(self) -> str (research.py:52)
class: MeasureResult (research.py:58)
class: IterationRecord (research.py:69)
class: EnhancementReport (research.py:80)
class: ColumnInfo (research.py:110)
class: TableMetadata (research.py:119)
class: TableSuggestion (research.py:129)
function: def _validate_ident(name) -> None (research.py:145)
function: def _fetch_table_metadata_from_runner(tables, execute_fn, default_catalog, default_schema) -> list (research.py:158)
function: def _make_mcp_execute_fn(client) (research.py:240)
function: def _extract_table_names(sql) -> list[tuple[str, str, str]] (research.py:257)
function: def _parse_trino_datasize(s) -> Optional[int] (research.py:291)
function: def _fetch_table_metadata(client, tables, default_catalog, default_schema) -> list[TableMetadata] (research.py:322)
from typing import NamedTuple
class: MemoryLimitResult (research.py:345)
function: def _fetch_per_node_memory_limit(client) -> MemoryLimitResult (research.py:360)
function: def _generate_table_suggestions(metadata) -> list[TableSuggestion] (research.py:429)
class: ExplainAnalyzeResult (research.py:552)
function: def _fetch_explain_analyze(client, sql, timeout_ms, label) -> ExplainAnalyzeResult (research.py:568)
function: def _parse_explain_stages(text) -> list[dict] (research.py:633)
function: def _resolve_query_tool(client) -> tuple[str, str] (research.py:724)
function: def _find_sql_param(tool_def) -> str (research.py:745)
function: def _execute_via_mcp(client, sql, timeout_ms, label) -> dict (research.py:754)
function: def _build_mcp_explain_runner(client) (research.py:812)
function: def _assemble_mcp_directions(client, sql, static_report) (research.py:845)
function: def _measure_mcp(client, sql, metric_key, runs, capture_rows, max_capture_rows, output, label, timeout_ms) -> MeasureResult (research.py:892)
function: def _re_normalize_value(v) -> object (research.py:1006)
function: def _re_normalize_row(row, exclude_indices) -> str (research.py:1024)
class: EquivDiff (research.py:1055)
function: def rows_equivalent(rows_a, rows_b, exclude_columns) -> tuple[bool, EquivDiff] (research.py:1066)
function: def _results_equivalent(rows_a, rows_b) -> tuple[bool, str] (research.py:1175)
function: def _run_mcp_plan_cost_loop() -> EnhancementReport (research.py:1190)
function: def generate_report(report, locale) -> str (research.py:1533)
function: def run_mcp_enhancement(client, sql, metric_key, max_iterations, verify_runs, provider, model, reasoning, output, build_prompt) -> EnhancementReport (research.py:1762)
function: def _fmt_metric_value(val) -> str (research.py:2450)
function: def _render_plan_card(output) -> None (research.py:2466)
function: def _render_sql_diff(output, old_sql, new_sql, max_lines) -> None (research.py:2501)
function: def _render_iteration_result(output) -> None (research.py:2534)
function: def _render_summary_card(output) -> None (research.py:2586)
function: def run_trino_research_via_mcp(provider, cfg, model, reasoning, output, build_prompt) -> None (research.py:2616)
from __future__ import annotations
import: logging
from dataclasses import dataclass
from typing import Any
from rich.markup import escape
from genie.skills.trino_query.sql_static.rule_ids import RULE_CARTESIAN_JOIN, RULE_JOIN_FIRST_FILTER_LATE, RULE_JOIN_KEY_COMPUTED, RULE_NULL_UNSAFE_EQUALS, RULE_PREDICATE_NOT_PUSHED_TO_CTE, RULE_REDUNDANT_CAST_CHAIN, RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY, RULE_SELECT_STAR, RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN, RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY
class: RuleGateItem (rule_gate.py:47)
class: RuleGateSummary (rule_gate.py:59)
  property: def counts(self) -> dict[str, int] (rule_gate.py:63)
  property: def has_findings(self) -> bool (rule_gate.py:77)
  property: def should_auto_iterate(self) -> bool (rule_gate.py:81)
function: def _sort_key(item) -> tuple[int, int, str, str, str] (rule_gate.py:175)
function: def _static_items(static_report) -> list[RuleGateItem] (rule_gate.py:185)
function: def _direction_items(directions) -> list[RuleGateItem] (rule_gate.py:226)
function: def build_rule_gate_summary(static_report, directions) -> RuleGateSummary (rule_gate.py:253)
function: def format_rule_gate_for_prompt(summary) -> str (rule_gate.py:265)
function: def render_rule_gate_summary(output, summary) -> None (rule_gate.py:291)
from __future__ import annotations
import: hashlib
import: json
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Sequence
from genie.skills.mcp_trino.cost_reader import CostReading, read_cost
from genie.skills.mcp_trino.research import rows_equivalent
from genie.skills.trino_query.detection_scan import DetectionFinding, scan_sql
class: ScanConfidence (trino_optimize.py:37)
class: ScanOutcome (trino_optimize.py:44)
function: def scan_with_confidence(sql, scan_fn) -> ScanOutcome (trino_optimize.py:51)
class: Baseline (trino_optimize.py:92)
class: Fragment (trino_optimize.py:102)
class: RewriteCandidate (trino_optimize.py:116)
class: VerifyResult (trino_optimize.py:127)
class: RecomposeStatus (trino_optimize.py:145)
class: RecomposeResult (trino_optimize.py:154)
class: VerifyVerdict (trino_optimize.py:166)
function: def _fragment_key(fragment) -> str (trino_optimize.py:177)
function: def _apply_rewrites(original_sql, active_rewrites) -> str (trino_optimize.py:186)
function: def _revert_until_clean(original_sql, candidates, initial_scan_outcome, scan_fn) -> RecomposeResult (trino_optimize.py:231)
function: def _normalize_rows_by_column_name(rows, exclude_column_names) -> tuple[list, tuple[int, ...]] (trino_optimize.py:290)
function: def baseline(sql, explain_runner, count_runner) -> Baseline (trino_optimize.py:329)
function: def _canonicalize_plan(plan_json) -> object (trino_optimize.py:378)
function: def decompose(sql, llm, cost_reader_fn) -> list[Fragment] (trino_optimize.py:395)
function: def _extract_fragments(sql) -> list[dict] (trino_optimize.py:499)
function: def _assign_subq_ordinals(fragments, counter) -> None (trino_optimize.py:581)
function: def _heuristic_monster_ids(fragments) -> list[str] (trino_optimize.py:592)
function: def _build_monster_prompt(fragments, heuristic_monsters) -> str (trino_optimize.py:603)
function: def _parse_monster_response(response, fragments) -> list[str] (trino_optimize.py:625)
function: def optimize(fragment, llm) -> RewriteCandidate (trino_optimize.py:644)
function: def recompose(original_sql, candidates, scan_fn) -> RecomposeResult (trino_optimize.py:767)
function: def verify(original_sql, recompose_result, query_runner, explain_runner, baseline_cost, exclude_column_names) -> VerifyResult (trino_optimize.py:864)
from __future__ import annotations
import: re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from genie.core.sql_extraction import extract_sql_from_reply
class: WriteOperation (write_analysis.py:18)
function: def _strip_sql_comments(sql) -> str (write_analysis.py:43)
function: def _split_sql_statements(sql) -> list[str] (write_analysis.py:49)
function: def _first_sql_keyword(statement) -> str (write_analysis.py:81)
function: def _strip_explain_prefix(statement) -> str (write_analysis.py:86)
function: def _classify_single_write_statement(statement, statement_count) -> WriteOperation | None (write_analysis.py:112)
function: def classify_write_operation(sql) -> WriteOperation | None (write_analysis.py:178)
function: def _static_findings_dict(static_report) -> dict (write_analysis.py:203)
function: def _empty_decompose_result(reason, error) -> dict (write_analysis.py:215)
function: def _make_advisory_llm_fn(provider, model, reasoning) (write_analysis.py:232)
function: def _advisory_cost_reader(sql) (write_analysis.py:257)
function: def _column_safe_candidates(candidates) (write_analysis.py:263)
function: def _semantic_safe_candidates(candidates) (write_analysis.py:305)
function: def _fragment_summary(fr) -> dict (write_analysis.py:339)
function: def _candidate_summary(cd) -> dict (write_analysis.py:349)
function: def _canonical_sql(sql) -> Optional[str] (write_analysis.py:359)
function: def _run_decompose_advisory(provider, model, reasoning, inner_sql, original_sql, analysis_is_ctas_inner) (write_analysis.py:370)
function: def _write_analysis_prompt(sql, operation, static_report) -> str (write_analysis.py:431)
function: def _normalize_display_sql(sql) -> str (write_analysis.py:479)
function: def _render_decompose_advisory(result) -> list (write_analysis.py:492)
function: def render_write_analysis_report(result) -> str (write_analysis.py:582)
function: def save_write_analysis_report(result, report_dir) -> Path (write_analysis.py:740)
function: def print_write_analysis_summary(output, result) -> None (write_analysis.py:748)
function: def run_write_analysis_only(provider, cfg, model, reasoning, sql, output, build_prompt) -> dict (write_analysis.py:759)
