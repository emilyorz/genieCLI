---
covers:
  - "genie/skills/oracle2trino/*.md"
  - "genie/skills/oracle2trino/*.py"
  - "genie/skills/oracle2trino/*.yaml"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Oracle-to-Trino SQL migration toolkit. Owns six skills registered under the `oracle2trino` group: mechanical sqlglot transpilation with confidence scoring, Oracle function/type lookup against a static YAML reference database, a Trino-limitations reference list, full stored-procedure analysis with connector-aware guidance, and a Trino SQL linter that catches Oracle residuals and Trino anti-patterns.

## Exports

# genie/skills/oracle2trino/SKILL.md: [tree-only: file:line citations required — LLM-written section]
from __future__ import annotations
import: json
import: re
from pathlib import Path
import: yaml
from genie.core.arg import Arg
from genie.core.registry import BaseSkill
from models import ConversionResult, UnsupportedConstruct
from genie.core.sql_patterns import ORACLE_CONSTRUCTS, compute_confidence
from genie.core.sql_utils import strip_comments_and_strings
function: def _load_db() -> dict (__init__.py:21)
function: def _truncate(text, limit) -> str (__init__.py:30)
function: def _sqlglot_transpile(sql) -> tuple[str, list[str], bool] (__init__.py:36)
function: def _detect_unsupported(sql) -> list[UnsupportedConstruct] (__init__.py:59)
class: TranspileSQL (__init__.py:84)
  method: def run(self, sql) -> str (__init__.py:95)
class: LookupOracleFunction (__init__.py:127)
  method: def run(self, oracle_name) -> str (__init__.py:135)
class: LookupOracleType (__init__.py:155)
  method: def run(self, oracle_type) -> str (__init__.py:163)
class: ListTrinoLimitations (__init__.py:175)
  method: def run(self) -> str (__init__.py:181)
class: AnalyzeOracleSP (__init__.py:192)
  method: def run(self, sql, connector) -> str (__init__.py:208)
class: LintTrinoSQL (__init__.py:262)
  method: def run(self, sql) -> str (__init__.py:281)
function: def register(registry) -> None (__init__.py:287)
# genie/skills/oracle2trino/data/oracle_trino_functions.yaml: [tree-only: file:line citations required — LLM-written section]
from __future__ import annotations
from dataclasses import dataclass, field
class: UnsupportedConstruct (models.py:8)
  method: def to_dict(self) -> dict (models.py:14)
class: ConversionResult (models.py:24)
  method: def to_dict(self) -> dict (models.py:31)

## Invariants

- `_load_db()` is a module-level singleton; `oracle_trino_functions.yaml` is loaded once into `_DB` and cached for the process lifetime (`__init__.py:18-27`). Hot-reloading requires a process restart.
- `_detect_unsupported()` scans the **original** SQL before transpilation, not the sqlglot output, so Oracle constructs that sqlglot silently mistranslates are still surfaced (`__init__.py:98-102`, `__init__.py:211-212`).
- `_sqlglot_transpile()` returns `(sql, [error], False)` on `ImportError` or any exception — callers must check the `success` bool, not the returned SQL string (`__init__.py:53-56`).
- `TranspileSQL.run` and `AnalyzeOracleSP.run` both return JSON serialised via `ConversionResult.to_dict()`, not plain text (`__init__.py:124`, `__init__.py:259`).
- `ConversionResult.confidence` is computed by `compute_confidence(unsupported)` from `genie.core.sql_patterns`; the value flows through `to_dict()` unmodified (`__init__.py:120`, `models.py:31`).
- `AnalyzeOracleSP` accepts `connector` choices `["hive", "iceberg", "delta", "generic"]`, defaulting to `"iceberg"` (`__init__.py:204-205`).
- `LintTrinoSQL.run` delegates entirely to `genie.core.lint_analyzer.analyze`; the skill itself contains no lint logic (`__init__.py:282-283`).
- `register()` must be called to make skills available; it registers exactly 6 skill instances in fixed order (`__init__.py:287-293`).

## Change log

572f7ff30399bed1a1a3c230918ba037ae874272: fix covers glob annotation — staleness scanner fnmatch confirmed matching data/oracle_trino_functions.yaml; Invariants citations re-verified against source
