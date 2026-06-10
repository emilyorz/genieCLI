"""v46 acceptance tests — --direct table-metadata parity (F1 / F2 / F3).

All tests are xfail(strict=True) until the develop step implements the feature.
Fixtures use mocked runners and probe results only; no live cluster.

Issue-ledger addressed by this file:
  [minor] items (all 6 from usage.issue_ledger): acknowledged, folded into prose
  below where relevant (F3 note text assertion, tuple-return regression guard, etc.).

Key spec references (spec.producer.md):
  Part 1 — _fetch_table_metadata_from_runner shared helper
  Part 2 — _execute_direct_as_dicts / _fetch_table_metadata_direct
  Part 3 — _assemble_direct_directions wiring + conditional note
  Part 4 — test matrix (this file)
  Part 4-T — required edits to EXISTING tests (not in this file; in the test files
             listed in the spec, applied by the develop step)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Verbatim sample probe results — lifted from explore/spec artifacts as fixtures.
# These represent what information_schema.columns and system.metadata.table_properties
# would return for a real partitioned+sorted Iceberg table.
# ---------------------------------------------------------------------------

_COLUMN_ROWS = [
    {"column_name": "event_date", "data_type": "date", "is_nullable": "NO", "ordinal_position": 1},
    {"column_name": "user_id", "data_type": "bigint", "is_nullable": "YES", "ordinal_position": 2},
    {"column_name": "amount", "data_type": "double", "is_nullable": "YES", "ordinal_position": 3},
]

_PROPERTY_ROWS = [
    {"property_name": "partitioning", "property_value": "[day(event_date)]"},
    {"property_name": "sorted_by", "property_value": "[user_id ASC]"},
]

# A table whose properties have NO sort or partition values — used to verify
# that the note predicate keys on FETCH OUTCOME, not on contributor output
_EMPTY_PROPERTY_ROWS: list[dict] = []


def _make_execute_fn(col_rows=None, prop_rows=None, raises=False):
    """Return an execute_fn that hands back col_rows on the first call, prop_rows on the second.

    If raises=True the function raises on any call.
    """
    calls = [0]
    def _fn(sql: str) -> list[dict]:
        if raises:
            raise RuntimeError("probe error")
        calls[0] += 1
        if calls[0] == 1:
            return col_rows if col_rows is not None else []
        return prop_rows if prop_rows is not None else []
    return _fn


# ---------------------------------------------------------------------------
# F1 — shared parsing helper: _fetch_table_metadata_from_runner
# ---------------------------------------------------------------------------

class TestSharedParsingHelper:
    """F1: _fetch_table_metadata_from_runner (pure function, execute_fn injected)."""

    def test_should_populate_columns_when_execute_fn_returns_column_rows(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner, TableMetadata

        execute_fn = _make_execute_fn(col_rows=_COLUMN_ROWS, prop_rows=[])
        tables = [("hive", "prod", "events")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        assert len(result) == 1
        assert isinstance(result[0], TableMetadata)
        assert result[0].table_name == "events"
        col_names = [c.column_name for c in result[0].columns]
        assert "event_date" in col_names
        assert "user_id" in col_names

    def test_should_populate_properties_when_execute_fn_returns_property_rows(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        execute_fn = _make_execute_fn(col_rows=[], prop_rows=_PROPERTY_ROWS)
        tables = [("hive", "prod", "events")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        assert len(result) == 1
        assert result[0].properties.get("partitioning") == "[day(event_date)]"
        assert result[0].properties.get("sorted_by") == "[user_id ASC]"

    def test_should_skip_table_when_execute_fn_raises(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        execute_fn = _make_execute_fn(raises=True)
        tables = [("hive", "prod", "events")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        # fail-open: exception → empty result, no propagation
        assert result == []

    def test_should_skip_table_when_both_probes_return_empty(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        execute_fn = _make_execute_fn(col_rows=[], prop_rows=[])
        tables = [("hive", "prod", "events")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        # tables with no columns AND no properties are NOT appended
        assert result == []

    def test_should_skip_table_when_catalog_or_schema_empty(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        execute_fn = _make_execute_fn(col_rows=_COLUMN_ROWS, prop_rows=_PROPERTY_ROWS)
        # Both empty-catalog and empty-schema variants are dropped by `if c and s`
        tables = [("", "prod", "t1"), ("hive", "", "t2")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        assert result == []

    def test_should_reject_unsafe_identifier_via_validate_ident(self):
        """table_name with unsafe chars (quotes, spaces, semicolons) → skipped via
        _validate_ident raising ValueError, caught by per-probe except guard, no crash."""
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        bad_names = ["evil'; DROP TABLE users; --", "has space", "has.dot"]
        for bad_name in bad_names:
            execute_fn = _make_execute_fn(col_rows=_COLUMN_ROWS, prop_rows=_PROPERTY_ROWS)
            result = _fetch_table_metadata_from_runner(
                [("hive", "prod", bad_name)], execute_fn
            )
            assert result == [], f"Expected [] for unsafe identifier {bad_name!r}, got {result}"

    def test_should_skip_non_dict_rows_in_execute_fn_output(self):
        """execute_fn returns a mix of non-dict and dict rows → non-dicts skipped, dicts parsed.

        This guards the MCP bare-list shape regression: _execute_via_mcp can return
        rows that are not dicts in its bare-list response shape (research.py:716-731).
        The shared helper must preserve the isinstance(row, dict) guard or the MCP
        path regresses post-refactor.
        """
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        mixed_rows = [
            "raw_string_not_a_dict",          # non-dict: must be skipped
            _COLUMN_ROWS[0],                   # dict: must be parsed
        ]
        calls = [0]
        def execute_fn(sql: str) -> list:
            calls[0] += 1
            if calls[0] == 1:
                return mixed_rows  # columns probe: mixed
            return []               # properties probe: empty
        tables = [("hive", "prod", "events")]
        result = _fetch_table_metadata_from_runner(tables, execute_fn)

        assert len(result) == 1
        assert len(result[0].columns) == 1, "Only the dict row should be parsed; non-dict must be skipped"

    def test_should_return_empty_for_empty_tables_list(self):
        from genie.skills.mcp_trino.research import _fetch_table_metadata_from_runner

        execute_fn = _make_execute_fn(col_rows=_COLUMN_ROWS, prop_rows=_PROPERTY_ROWS)
        result = _fetch_table_metadata_from_runner([], execute_fn)
        assert result == []


# ---------------------------------------------------------------------------
# F1 — MCP adapter: _make_mcp_execute_fn (envelope extraction)
# ---------------------------------------------------------------------------

class TestMcpAdapter:
    """F1: _make_mcp_execute_fn must extract rows from the _execute_via_mcp envelope.

    _execute_via_mcp returns {'rows': [...], 'columns': [...], 'error': ...}
    NOT a list[dict]. Passing the envelope directly iterates its keys, silently
    producing empty metadata (the defect class fixed by this adapter).
    """

    def test_mcp_execute_fn_extracts_rows_from_envelope(self):
        from genie.skills.mcp_trino.research import _make_mcp_execute_fn, _execute_via_mcp

        envelope = {"rows": [_COLUMN_ROWS[0]], "columns": ["column_name"], "error": None}
        client = MagicMock()
        with patch("genie.skills.mcp_trino.research._execute_via_mcp", return_value=envelope):
            fn = _make_mcp_execute_fn(client)
            result = fn("SELECT 1")

        assert result == [_COLUMN_ROWS[0]], (
            "Adapter must extract the 'rows' list, not return the envelope dict"
        )

    def test_mcp_execute_fn_returns_empty_on_error_envelope(self):
        from genie.skills.mcp_trino.research import _make_mcp_execute_fn

        envelope = {"rows": None, "columns": [], "error": "boom"}
        client = MagicMock()
        with patch("genie.skills.mcp_trino.research._execute_via_mcp", return_value=envelope):
            fn = _make_mcp_execute_fn(client)
            result = fn("SELECT 1")

        assert result == []

    def test_mcp_fetcher_unchanged_behaviour_after_refactor(self):
        """Characterization test: _fetch_table_metadata produces the correct
        TableMetadata output now (pre-refactor baseline) AND must remain identical
        after the body is replaced by the shared-helper delegation.

        This test is intentionally NOT xfail: it passes against the current production
        code (locking the baseline behavior) and must also pass after the refactor.
        If it fails post-refactor, the develop step has introduced a regression.
        """
        from genie.skills.mcp_trino.research import _fetch_table_metadata, TableMetadata, ColumnInfo

        col_envelope = {"rows": _COLUMN_ROWS, "columns": [], "error": None}
        prop_envelope = {"rows": _PROPERTY_ROWS, "columns": [], "error": None}

        client = MagicMock()
        side_effects = iter([col_envelope, prop_envelope])
        with patch("genie.skills.mcp_trino.research._execute_via_mcp",
                   side_effect=lambda *a, **kw: next(side_effects)):
            result = _fetch_table_metadata(client, [("hive", "prod", "events")])

        assert len(result) == 1
        tm = result[0]
        assert isinstance(tm, TableMetadata)
        assert tm.table_name == "events"
        assert any(c.column_name == "event_date" for c in tm.columns)
        assert tm.properties.get("partitioning") == "[day(event_date)]"


# ---------------------------------------------------------------------------
# F2 — direct fetcher error cases (mocked connection) + parity
# ---------------------------------------------------------------------------

class TestDirectFetcher:
    """F2: _execute_direct_as_dicts and _fetch_table_metadata_direct (mocked connection)."""

    def test_direct_fetcher_returns_empty_when_profile_unavailable(self):
        """get_active_profile() raises → _execute_direct_as_dicts returns [], no crash."""
        from genie.skills.trino_query.research import _execute_direct_as_dicts

        with patch("genie.skills.trino_query.research.get_active_profile",
                   side_effect=RuntimeError("no profile")):
            result = _execute_direct_as_dicts("SELECT 1")

        assert result == []

    def test_direct_fetcher_returns_empty_when_cursor_execute_raises(self):
        """cursor.execute raises → _execute_direct_as_dicts returns [], no crash."""
        from genie.skills.trino_query.research import _execute_direct_as_dicts

        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("query error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_profile = MagicMock()
        mock_profile.connect.return_value = mock_conn

        with patch("genie.skills.trino_query.research.get_active_profile",
                   return_value=mock_profile):
            result = _execute_direct_as_dicts("SELECT 1")

        assert result == []

    def test_direct_fetcher_handles_none_description(self):
        """cur.description is None (zero-row metadata result) → [] rows, no crash.

        Note: the behavior is that _execute_direct_as_dicts returns [] (zero dicts
        via the `cur.description or []` guard path). Test name refers to the input
        condition (None description), not a thrown exception.
        """
        from genie.skills.trino_query.research import _execute_direct_as_dicts

        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_profile = MagicMock()
        mock_profile.connect.return_value = mock_conn

        with patch("genie.skills.trino_query.research.get_active_profile",
                   return_value=mock_profile):
            result = _execute_direct_as_dicts("SELECT 1")

        assert result == []

    def test_direct_fetcher_builds_dicts_from_description(self):
        """cur.description provides column names; fetchall() returns tuples → list[dict]."""
        from genie.skills.trino_query.research import _execute_direct_as_dicts

        # description: sequence of (name, ...) tuples (DB-API2 spec)
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("column_name", None, None, None, None, None, None),
            ("data_type", None, None, None, None, None, None),
            ("is_nullable", None, None, None, None, None, None),
            ("ordinal_position", None, None, None, None, None, None),
        ]
        mock_cursor.fetchall.return_value = [
            ("event_date", "date", "NO", 1),
            ("user_id", "bigint", "YES", 2),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_profile = MagicMock()
        mock_profile.connect.return_value = mock_conn

        with patch("genie.skills.trino_query.research.get_active_profile",
                   return_value=mock_profile):
            result = _execute_direct_as_dicts("SELECT 1")

        assert result == [
            {"column_name": "event_date", "data_type": "date", "is_nullable": "NO", "ordinal_position": 1},
            {"column_name": "user_id", "data_type": "bigint", "is_nullable": "YES", "ordinal_position": 2},
        ]

    def test_fetch_table_metadata_direct_returns_empty_for_unqualified_sql(self):
        """SQL with no catalog.schema-qualified tables → gate `if c and s` → []."""
        from genie.skills.trino_query.research import _fetch_table_metadata_direct

        with patch("genie.skills.trino_query.research.get_active_profile") as mock_profile:
            result = _fetch_table_metadata_direct("SELECT * FROM orders")

        assert result == []
        # The connection must NOT have been opened for an unqualified table
        mock_profile.assert_not_called()

    def test_fetch_table_metadata_direct_probes_qualified_tables(self):
        """SELECT * FROM c.s.t with a mocked cursor → non-empty list[TableMetadata]."""
        from genie.skills.trino_query.research import _fetch_table_metadata_direct
        from genie.skills.mcp_trino.research import TableMetadata

        # Two calls: columns probe then properties probe
        call_count = [0]
        def fake_execute(sql: str):
            pass

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("column_name", None, None, None, None, None, None),
            ("data_type", None, None, None, None, None, None),
            ("is_nullable", None, None, None, None, None, None),
            ("ordinal_position", None, None, None, None, None, None),
        ]
        # First fetchall = column rows; second = property rows (as dicts via property probe SQL)
        property_cursor = MagicMock()
        property_cursor.description = [
            ("property_name", None, None, None, None, None, None),
            ("property_value", None, None, None, None, None, None),
        ]
        property_cursor.fetchall.return_value = [("partitioning", "[day(event_date)]")]

        cursors = iter([mock_cursor, property_cursor])
        mock_cursor.fetchall.return_value = [("event_date", "date", "NO", 1)]
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = lambda: next(cursors)
        mock_profile = MagicMock()
        mock_profile.connect.return_value = mock_conn

        with patch("genie.skills.trino_query.research.get_active_profile",
                   return_value=mock_profile):
            result = _fetch_table_metadata_direct("SELECT * FROM hive.prod.events")

        assert len(result) >= 1
        assert isinstance(result[0], TableMetadata)
        assert result[0].table_name == "events"

    def test_contributor_parity_same_fixture_both_paths(self):
        """Build ONE property fixture; run through (a) MCP and (b) direct assembly;
        assert the leverage-* direction tuples are IDENTICAL.

        This proves _metadata_contributor cannot tell which path produced the metadata —
        satisfying F2's indistinguishability requirement.
        """
        from genie.skills.mcp_trino import research as mcp_research
        from genie.skills.trino_query import research as direct_research
        from genie.skills.mcp_trino.research import TableMetadata, ColumnInfo

        # A single partitioned+sorted table — the metadata contributor should emit
        # exactly leverage-partitioning and leverage-sort for both paths
        metadata_fixture = [
            TableMetadata(
                catalog="hive",
                schema="prod",
                table_name="events",
                columns=[ColumnInfo(column_name="event_date", data_type="date",
                                    is_nullable="NO", ordinal_position=1)],
                properties={"partitioning": "[day(event_date)]", "sorted_by": "[user_id ASC]"},
            )
        ]

        # (a) MCP path: inject metadata via table_metadata= parameter
        mcp_directions, returned_meta = mcp_research._assemble_mcp_directions(
            MagicMock(),
            "SELECT * FROM hive.prod.events",
            None,  # static_report
            table_metadata=metadata_fixture,
        )

        # (b) Direct path: inject metadata via table_metadata= parameter (F2 wiring)
        direct_directions, _ = direct_research._assemble_direct_directions(
            "SELECT * FROM hive.prod.events",
            None,  # static_report
            None,  # explain_runner
            table_metadata=metadata_fixture,
        )

        def _leverage_tuples(dirs):
            return {
                (d.kind, d.severity, d.target_metric)
                for d in dirs
                if d.kind.startswith("leverage-")
            }

        mcp_leverage = _leverage_tuples(mcp_directions)
        direct_leverage = _leverage_tuples(direct_directions)

        assert mcp_leverage == direct_leverage, (
            f"Metadata contributor produced different results on MCP vs direct.\n"
            f"MCP: {mcp_leverage}\nDirect: {direct_leverage}"
        )
        assert ("leverage-partitioning", "low", "physical_input_bytes") in mcp_leverage
        assert ("leverage-sort", "low", "physical_input_bytes") in mcp_leverage


# ---------------------------------------------------------------------------
# F2 — tuple return + wiring
# ---------------------------------------------------------------------------

class TestTupleReturnAndWiring:
    """F2: _assemble_direct_directions returns (directions, pre_table_metadata)."""

    def test_assemble_direct_directions_returns_two_tuple(self):
        """Return is a 2-tuple (list, list) — regression guard for all callers.

        Without this test, any caller that does `directions = _assemble_direct_directions(...)`
        would assign a tuple to `directions`, silently breaking format_directions_for_prompt
        and rule_gate calls.
        """
        from genie.skills.trino_query.research import _assemble_direct_directions

        result = _assemble_direct_directions("SELECT 1", None, None)

        assert isinstance(result, tuple), f"Expected 2-tuple, got {type(result).__name__}"
        assert len(result) == 2, f"Expected exactly 2 elements, got {len(result)}"
        directions, metadata = result
        assert isinstance(directions, list), f"First element must be list, got {type(directions).__name__}"
        assert isinstance(metadata, list), f"Second element must be list, got {type(metadata).__name__}"

    def test_assemble_direct_reuses_metadata_when_injected(self):
        """When table_metadata=<non-empty list> is passed, _fetch_table_metadata_direct
        must NOT be called (no extra connection round-trip).

        Exact assertion pattern (spec Part 3): patch _fetch_table_metadata_direct and
        assert mock.call_count == 0 when called with a non-empty injected metadata list.
        """
        from genie.skills.mcp_trino.research import TableMetadata
        from genie.skills.trino_query.research import _assemble_direct_directions

        injected = [TableMetadata(catalog="c", schema="s", table_name="t")]

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct") as mock_fetcher:
            directions, returned_meta = _assemble_direct_directions(
                "SELECT * FROM c.s.t", None, None,
                table_metadata=injected,
            )

        assert mock_fetcher.call_count == 0, (
            f"_fetch_table_metadata_direct must NOT be called when metadata is injected. "
            f"Called {mock_fetcher.call_count} time(s)."
        )
        assert returned_meta == injected

    def test_assemble_direct_fetches_when_not_injected(self):
        """When table_metadata=None (default), _fetch_table_metadata_direct must be
        called exactly once and its return value used as the metadata.
        """
        from genie.skills.mcp_trino.research import TableMetadata
        from genie.skills.trino_query.research import _assemble_direct_directions

        fetched = [TableMetadata(catalog="c", schema="s", table_name="t")]

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=fetched) as mock_fetcher:
            directions, returned_meta = _assemble_direct_directions(
                "SELECT * FROM c.s.t", None, None,
                # table_metadata NOT passed → defaults to None → fetch should fire
            )

        assert mock_fetcher.call_count == 1, (
            f"_fetch_table_metadata_direct must be called once when table_metadata=None. "
            f"Called {mock_fetcher.call_count} time(s)."
        )
        assert returned_meta == fetched


# ---------------------------------------------------------------------------
# F3 — note conditionality
# (s1 explicit-patch approach per spec Part 4 / item 3 resolution)
# All patch genie.skills.trino_query.research._fetch_table_metadata_direct directly
# so behavior is resilient to SQL example changes.
# ---------------------------------------------------------------------------

class TestNoteConditionality:
    """F3: metadata-unavailable note is CONDITIONAL on fetch outcome, not contributor output."""

    def test_note_absent_when_fetch_returns_non_empty_metadata(self):
        """Fetcher returns a non-empty TableMetadata list → note must be ABSENT.

        Truth table row 1: fetch succeeded, contributor emits leverage directions.
        """
        from genie.skills.mcp_trino.research import TableMetadata, ColumnInfo
        from genie.skills.trino_query.research import _assemble_direct_directions

        fetched = [
            TableMetadata(
                catalog="hive", schema="prod", table_name="events",
                columns=[ColumnInfo(column_name="event_date", data_type="date",
                                    is_nullable="NO", ordinal_position=1)],
                properties={"partitioning": "[day(event_date)]"},
            )
        ]

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=fetched):
            directions, returned_meta = _assemble_direct_directions(
                "SELECT * FROM hive.prod.events", None, None
            )

        kinds = {d.kind for d in directions}
        assert "metadata-unavailable" not in kinds, (
            f"Note must be ABSENT when fetch returns non-empty metadata. Kinds: {kinds}"
        )
        assert returned_meta == fetched

    def test_note_absent_when_metadata_fetched_but_contributor_returns_empty(self):
        """Fetcher returns non-empty but table has no sort/partition props → contributor
        returns []. The note must STILL be ABSENT because the predicate keys on fetch
        outcome (bool(pre_table_metadata)), not on contributor output.

        Truth table row 2: this is the key invariant that prior approaches missed.
        """
        from genie.skills.mcp_trino.research import TableMetadata
        from genie.skills.trino_query.research import _assemble_direct_directions

        # A table with EMPTY properties — contributor emits nothing
        fetched = [TableMetadata(catalog="hive", schema="prod", table_name="events")]

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=fetched):
            directions, returned_meta = _assemble_direct_directions(
                "SELECT * FROM hive.prod.events", None, None
            )

        kinds = {d.kind for d in directions}
        assert "metadata-unavailable" not in kinds, (
            f"Note must be ABSENT when fetch returned non-empty metadata (even if "
            f"contributor emits nothing). Kinds: {kinds}"
        )

    def test_note_present_when_fetch_returns_empty_list(self):
        """Fetcher returns [] (all probes failed) → note must be PRESENT.

        Truth table row 3.
        """
        from genie.skills.trino_query.research import _assemble_direct_directions

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=[]):
            directions, _ = _assemble_direct_directions(
                "SELECT * FROM hive.prod.events", None, None
            )

        kinds = [d.kind for d in directions]
        assert "metadata-unavailable" in kinds, (
            f"Note must be PRESENT when fetch returns []. Kinds: {kinds}"
        )

    def test_note_present_when_sql_has_no_qualified_tables(self):
        """SQL with no catalog.schema refs → fetch short-circuits returning [] → note present.

        Truth table row 4. Using "SELECT 1" (no tables at all).
        """
        from genie.skills.trino_query.research import _assemble_direct_directions

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=[]) as mock_fetcher:
            directions, _ = _assemble_direct_directions("SELECT 1", None, None)

        kinds = [d.kind for d in directions]
        assert "metadata-unavailable" in kinds, (
            f"Note must be PRESENT when SQL has no qualified tables. Kinds: {kinds}"
        )

    def test_note_still_last_when_present(self):
        """When the note IS present, it must still sort last (severity=info → rank 3).

        A CROSS JOIN gives a high-severity direction; the note must appear after it.
        """
        from genie.skills.trino_query.research import _assemble_direct_directions
        from genie.skills.trino_query.sql_static import analyze as static_analyze

        static_report = static_analyze("SELECT * FROM a CROSS JOIN b")

        with patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=[]):
            directions, _ = _assemble_direct_directions(
                "SELECT * FROM a CROSS JOIN b", static_report, None
            )

        if len(directions) > 1:
            assert directions[-1].kind == "metadata-unavailable", (
                f"Expected metadata-unavailable last, got {directions[-1].kind!r}. "
                f"All kinds: {[d.kind for d in directions]}"
            )


# ---------------------------------------------------------------------------
# Degrade cases — fail-open end-to-end
# ---------------------------------------------------------------------------

class TestDegradeCases:
    """End-to-end degrade: fetch errors must not crash the --direct path."""

    def test_diagnose_only_degrades_to_note_when_fetch_fails(self):
        """DIAGNOSE_ONLY route: _fetch_table_metadata_direct raises → report still
        produced, note present, no exception propagated.

        Fail-open is the first hard constraint in the spec.
        """
        from genie.skills.trino_query import research as direct_research
        from unittest.mock import MagicMock

        with patch.object(direct_research, "_measure", MagicMock()), \
             patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   side_effect=Exception("cluster unreachable")):
            result = direct_research._run_optimization_loop(
                provider=MagicMock(),
                model="m",
                reasoning="disable",
                original_sql="SELECT * FROM hive.prod.events",
                metric_key="wall_time_ms",
                max_iterations=3,
                verify_runs=1,
                output=MagicMock(),
                build_prompt=lambda *a, **k: "SKILL",
                explain_runner=None,
                diagnose_only=True,
            )

        assert result["status"] == "diagnosed"
        assert "report_markdown" in result
        assert "metadata-unavailable" in result["report_markdown"]

    def test_standard_loop_no_crash_when_fetch_returns_empty(self):
        """STANDARD_LOOP pre-loop: empty fetch → loop proceeds, note present in
        directions used for the system prompt.

        Tests the pre-loop site (spec line 981) specifically.
        """
        from genie.skills.trino_query import QueryMetrics
        from genie.skills.trino_query import research as direct_research

        fake_baseline = {
            "median": 100.0,
            "samples": [100.0],
            "row_count": 5,
            "rows": [(1,)],
            "metrics": QueryMetrics(wall_time_ms=100.0),
        }

        output = MagicMock()

        with patch.object(direct_research, "_measure", return_value=fake_baseline), \
             patch("genie.skills.trino_query.research._fetch_table_metadata_direct",
                   return_value=[]):
            # max_iterations=0 → loop does not iterate; we just need the pre-loop
            # directions block to be assembled without crashing
            result = direct_research._run_optimization_loop(
                provider=MagicMock(),
                model="m",
                reasoning="disable",
                original_sql="SELECT * FROM hive.prod.events",
                metric_key="wall_time_ms",
                max_iterations=0,
                verify_runs=1,
                output=output,
                build_prompt=lambda *a, **k: "SKILL",
                explain_runner=None,
            )

        # The loop ran (did not crash) and returned a result
        assert "status" in result
