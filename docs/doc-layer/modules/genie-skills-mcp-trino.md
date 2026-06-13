---
covers:
  - "genie/skills/mcp_trino/*.md"
  - "genie/skills/mcp_trino/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

`genie/skills/mcp_trino` is the MCP-backed Trino query enhancement skill package.
It connects to a Trino MCP server via JSON-RPC 2.0 over Streamable HTTP, dynamically
registers each server tool as a genieCLI skill, and drives the full `/trino-research`
pipeline: pre-flight safety checks, static rule gate, pre-execution diagnosis (join
analysis, memory pressure, partition metadata), iterative AI-proposed SQL rewrites,
result equivalence verification, and write-advisory analysis for DML/DDL queries.
The module also owns the five-stage `trino_optimize` pipeline (baseline / decompose /
optimize / recompose / verify) used by the structured optimization path.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-skills-mcp-trino.md

- run_mcp_enhancement: Top-level iterative optimization loop; returns EnhancementReport.
- run_trino_research_via_mcp: Entry point for `/trino-research` MCP path.
- pre_execution_diagnosis: Aggregates static, join, memory, and metadata directions.
- build_rule_gate_summary: Merges static findings and directions into RuleGateSummary.
- McpClient: JSON-RPC 2.0 session-aware HTTP client for the MCP server.
- load_mcp_config: Config loader; env vars override TOML, TOML overrides JSON file.
- run_write_analysis_only: Advisory decompose path for DML/DDL (non-executing).
- rows_equivalent: Row-set equality check used in candidate verification.

## Invariants

- MCP skills are only registered when `config.enabled` is True — `__init__.py:117` — `if not config.enabled:`
- `McpClient` uses `Mcp-Session-Id` header for session continuity — `client.py:144` — `headers["Mcp-Session-Id"] = self._session_id`
- Default MCP endpoint is `http://localhost:8811/mcp` — `client.py:30` — `url: str = "http://localhost:8811/mcp"`
- Config priority: env vars > TOML > JSON > defaults — `client.py:40` — `Load MCP Trino config from multiple sources (highest priority first):`
- `check_read_only` blocks DML/DDL before any execution — `preflight.py:17` — `READ_ONLY_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC"}`
- `_combine_cost` treats partial EXPLAIN results without fake-zero distortion — `preflight.py:162` — `def _combine_cost(rows: Optional[int], bytes_: Optional[int]) -> Optional[int]:`
- `NoDataDetected` short-circuits the iteration loop to the static-analysis path — `preflight.py:283` — `class NoDataDetected(RuntimeError):`
- `rule_gate` does not rewrite SQL; it only classifies signals — `rule_gate.py:3` — `This module deliberately does not rewrite SQL.`
- `trino_optimize` public functions never raise; all degrade to typed outcomes — `trino_optimize.py:7` — `No public function raises; all degrade to typed unavailable/unverified outcomes.`
- `read_cost` never propagates exceptions to callers — `cost_reader.py:43` — `NEVER raises to the caller.`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: Initial card created for doc-bootstrap run.
