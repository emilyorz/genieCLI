from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
class: Arg (arg.py:9)
from __future__ import annotations
import: json
import: os
from pathlib import Path
from typing import Any
function: def load(overrides) -> dict (config.py:39)
function: def save(cfg) -> None (config.py:65)
function: def _merge_json(cfg) -> None (config.py:74)
function: def _merge_toml(cfg) -> None (config.py:84)
function: def _merge_env(cfg) -> None (config.py:108)
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from genie.core.provider import Provider
class: OutputSink (context.py:11)
  method: def progress(self, msg) -> None (context.py:12)
  method: def result(self, data) -> None (context.py:13)
  method: def stream(self, text) -> None (context.py:14)
  method: def error(self, msg, code) -> None (context.py:15)
  method: def table(self, rows, headers) -> None (context.py:16)
  method: def confirm(self, prompt) -> bool (context.py:17)
  method: def markdown(self, text) -> None (context.py:18)
  method: def print(self, msg) -> None (context.py:19)
  method: def tool_call(self, name, args) -> None (context.py:20)
  method: def tool_result(self, result) -> None (context.py:21)
class: SkillContext (context.py:25)
from __future__ import annotations
from dataclasses import dataclass, field
from genie.core.model_profiles import ModelProfile, estimate_tokens, get_profile
class: ContextManager (context_manager.py:23)
  method: def __post_init__(self) -> None (context_manager.py:30)
  property: def context_window(self) -> int (context_manager.py:34)
  property: def available_for_history(self) -> int (context_manager.py:38)
  method: def estimate_history_tokens(self, history) -> int (context_manager.py:43)
  method: def should_prune(self, history) -> bool (context_manager.py:61)
  method: def prune_history(self, history) -> list[dict] (context_manager.py:66)
  method: def truncate_tool_result(self, result) -> str (context_manager.py:102)
  method: def _truncate_message(self, msg) -> dict (context_manager.py:113)
  method: def _summarize_messages(self, messages, max_chars) -> str (context_manager.py:131)
  method: def context_status(self, history) -> dict (context_manager.py:172)
from __future__ import annotations
import: logging
from dataclasses import dataclass, field
from genie.core.lint_rules import ALL_RULES, Finding
class: LintResult (lint_analyzer.py:13)
  method: def to_dict(self) -> dict (lint_analyzer.py:19)
function: def _compute_score(findings, parse_error) -> str (lint_analyzer.py:37)
function: def _make_summary(findings) -> str (lint_analyzer.py:53)
function: def analyze(sql) -> LintResult (lint_analyzer.py:60)
from __future__ import annotations
import: logging
import: re
from dataclasses import dataclass
from typing import Any
from genie.core.sql_patterns import get_construct_meta, get_construct_pattern
from genie.core.sql_utils import strip_comments_and_strings
class: Finding (lint_rules.py:16)
function: def _line_of(sql, pattern, flags) -> int (lint_rules.py:26)
function: def _all_lines_of(sql, pattern, flags) -> list[int] (lint_rules.py:31)
function: def _check_oracle_residual(sql, construct, rule_id) -> list[Finding] (lint_rules.py:37)
function: def check_nvl(sql, statements) -> list[Finding] (lint_rules.py:62)
function: def check_decode(sql, statements) -> list[Finding] (lint_rules.py:67)
function: def check_plus_join(sql, statements) -> list[Finding] (lint_rules.py:72)
function: def check_rownum(sql, statements) -> list[Finding] (lint_rules.py:77)
function: def check_sysdate(sql, statements) -> list[Finding] (lint_rules.py:82)
function: def check_select_star(sql, statements) -> list[Finding] (lint_rules.py:89)
function: def check_implicit_cross_join(sql, statements) -> list[Finding] (lint_rules.py:144)
function: def check_leading_wildcard_like(sql, statements) -> list[Finding] (lint_rules.py:165)
function: def check_count_distinct(sql, statements) -> list[Finding] (lint_rules.py:180)
function: def check_correlated_subquery(sql, statements) -> list[Finding] (lint_rules.py:194)
function: def check_missing_partition_filter(sql, statements) -> list[Finding] (lint_rules.py:259)
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import: re
class: ModelProfile (model_profiles.py:17)
function: def get_profile(model_name) -> ModelProfile (model_profiles.py:67)
function: def estimate_tokens(text, model_name) -> int (model_profiles.py:84)
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable
class: Delta (provider.py:9)
class: CompletionRequest (provider.py:15)
class: ProviderCapabilities (provider.py:24)
class: Provider (provider.py:31)
  property: def name(self) -> str (provider.py:33)
  method: def complete(self, req) -> Iterator[Delta] (provider.py:35)
  method: def complete_text(self, req) -> str (provider.py:37)
  method: def capabilities(self) -> ProviderCapabilities (provider.py:41)
from __future__ import annotations
import: importlib
import: importlib.util
import: sys
from pathlib import Path
from typing import TYPE_CHECKING
class: BaseSkill (registry.py:14)
  method: def run(self, **kwargs) -> str (registry.py:29)
  method: def validate(self, kwargs) -> tuple[bool, str | None] (registry.py:32)
  method: def spec(self) -> dict (registry.py:45)
  method: def tools(self) -> list[dict] (registry.py:63)
  method: def run_tool(self, name, args, ctx) -> str (registry.py:66)
  method: def contribute_commands(self, app) -> None (registry.py:81)
class: SkillRegistry (registry.py:85)
  classmethod: def register(cls, skill) -> None (registry.py:93)
  classmethod: def register_instructions(cls, group, body) -> None (registry.py:97)
  classmethod: def get_instructions(cls, group) -> str (registry.py:103)
  classmethod: def all_instructions(cls) -> dict[str, str] (registry.py:107)
  classmethod: def register_clear_hook(cls, hook) -> None (registry.py:111)
  classmethod: def clear(cls) -> None (registry.py:122)
  classmethod: def get(cls, name) -> BaseSkill | None (registry.py:135)
  classmethod: def all(cls, tier) -> list[BaseSkill] (registry.py:139)
  classmethod: def all_tools(cls) -> list[dict] (registry.py:158)
  classmethod: def run_tool(cls, name, args, ctx) -> str (registry.py:165)
  classmethod: def discover(cls, paths) -> None (registry.py:173)
  classmethod: def discover_legacy(cls, module_name) -> None (registry.py:197)
function: def _load_skill_package(skill_dir) -> None (registry.py:223)
function: def parse_skill_md(path) -> dict (registry.py:247)
function: def parse_skill_md_body(path) -> str (registry.py:268)
from __future__ import annotations
import: re
from typing import Optional
function: def extract_sql_from_reply(reply) -> Optional[str] (sql_extraction.py:8)
function: def extract_ctas_inner_select(sql) -> Optional[str] (sql_extraction.py:29)
function: def rewrap_ctas_inner_select(original_ctas_sql, new_inner_sql) -> Optional[str] (sql_extraction.py:66)
function: def query_output_columns(sql) -> Optional[tuple] (sql_extraction.py:113)
function: def queries_structurally_equivalent(sql1, sql2) -> Optional[bool] (sql_extraction.py:165)
from __future__ import annotations
function: def get_construct_meta(construct) -> dict | None (sql_patterns.py:209)
function: def get_construct_pattern(construct) -> str | None (sql_patterns.py:214)
function: def compute_confidence(unsupported) -> float (sql_patterns.py:220)
from __future__ import annotations
function: def strip_comments_and_strings(sql) -> str (sql_utils.py:5)
from __future__ import annotations
import: json
import: re
function: def parse_tool_call(text) -> dict | None (tool_call.py:8)
function: def extract_memory(text) -> str (tool_call.py:45)
function: def normalize_result(result) -> str (tool_call.py:50)
