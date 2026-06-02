"""R9 join-first-filter-late: later consumer WHERE filters rows after a join."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import Finding
from ..rule_ids import RULE_JOIN_FIRST_FILTER_LATE


_NONDETERMINISTIC_RE = re.compile(
    r"\b(rand|random|uuid|current_timestamp|current_time|current_date|now)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _BaseRef:
    source_alias: str
    column: str


def _select_has_semantic_boundary(select, exp) -> bool:
    if select.args.get("where") is not None:
        return True
    if select.args.get("group") is not None or select.args.get("having") is not None:
        return True
    if select.args.get("distinct") is not None or select.args.get("qualify") is not None:
        return True
    if select.args.get("limit") is not None or select.args.get("offset") is not None:
        return True
    if any(isinstance(node, (exp.Union, exp.Except, exp.Intersect, exp.Window, exp.AggFunc)) for node in select.walk()):
        return True
    return False


def _lineage_output_name(projection) -> str | None:
    alias = getattr(projection, "alias", None)
    if alias:
        return str(alias)
    name = getattr(projection, "name", None)
    return str(name) if name else None


def _single_source(select):
    from_ = select.args.get("from_")
    joins = select.args.get("joins") or []
    if from_ is None or from_.this is None or joins:
        return None
    return from_.this


def _source_alias(source) -> str:
    alias = getattr(source, "alias_or_name", None)
    return str(alias or "")


def _resolve_source_select(source, cte_map, exp):
    if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
        return source.this
    if isinstance(source, exp.Table):
        return cte_map.get(source.name)
    return None


def _resolve_joined_producer_lineage(select, exp) -> dict[str, _BaseRef] | None:
    if _select_has_semantic_boundary(select, exp):
        return None
    joins = select.args.get("joins") or []
    if not joins:
        return None

    base_sources = []
    from_ = select.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return None
    base_sources.append(from_.this)
    for join in joins:
        kind = str(join.args.get("kind") or "").upper()
        side = str(join.args.get("side") or "").upper()
        if kind not in ("", "INNER") or side:
            return None
        if join.args.get("on") is None and not join.args.get("using"):
            return None
        if not isinstance(join.this, exp.Table):
            return None
        base_sources.append(join.this)

    aliases = {_source_alias(source) for source in base_sources}
    if "" in aliases or len(aliases) != len(base_sources):
        return None

    lineage: dict[str, _BaseRef] = {}
    for projection in select.expressions or []:
        if isinstance(projection, exp.Star) or projection.find(exp.Star):
            return None
        output_name = _lineage_output_name(projection)
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        if not output_name or not isinstance(inner, exp.Column) or not inner.table:
            return None
        if inner.table not in aliases:
            return None
        ref = _BaseRef(inner.table, inner.name)
        prior = lineage.get(output_name)
        if prior is not None and prior != ref:
            return None
        lineage[output_name] = ref
    return lineage or None


def _resolve_producer_lineage(select, cte_map, exp, remaining_hops: int) -> dict[str, _BaseRef] | None:
    joined_lineage = _resolve_joined_producer_lineage(select, exp)
    if joined_lineage is not None:
        return joined_lineage
    if remaining_hops <= 0 or _select_has_semantic_boundary(select, exp):
        return None

    source = _single_source(select)
    if source is None:
        return None
    source_alias = _source_alias(source)
    upstream = _resolve_source_select(source, cte_map, exp)
    if not source_alias or upstream is None:
        return None

    upstream_lineage = _resolve_producer_lineage(upstream, cte_map, exp, remaining_hops - 1)
    if upstream_lineage is None:
        return None

    lineage: dict[str, _BaseRef] = {}
    for projection in select.expressions or []:
        if isinstance(projection, exp.Star) or projection.find(exp.Star):
            return None
        output_name = _lineage_output_name(projection)
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        if not output_name or not isinstance(inner, exp.Column):
            return None
        if inner.table and inner.table != source_alias:
            return None
        ref = upstream_lineage.get(inner.name)
        if ref is None:
            return None
        prior = lineage.get(output_name)
        if prior is not None and prior != ref:
            return None
        lineage[output_name] = ref
    return lineage or None


def _flatten_and(expr, exp) -> list:
    if isinstance(expr, exp.And):
        return _flatten_and(expr.left, exp) + _flatten_and(expr.right, exp)
    return [expr]


def _predicate_base_refs(predicate, consumer_source_alias: str, producer_lineage: dict[str, _BaseRef], exp) -> list[_BaseRef] | None:
    if any(isinstance(node, (exp.Select, exp.Subquery, exp.Exists)) for node in predicate.walk()):
        return None
    if _NONDETERMINISTIC_RE.search(predicate.sql()):
        return None
    refs: list[_BaseRef] = []
    for column in predicate.find_all(exp.Column):
        if column.table and column.table != consumer_source_alias:
            return None
        ref = producer_lineage.get(column.name)
        if ref is None:
            return None
        refs.append(ref)
    return refs or None


def _token_signature(sql: str, tokenizer) -> tuple[str, ...]:
    return tuple(token.text.upper() for token in tokenizer.tokenize(sql))


def _find_where_line(sql: str, where_expr) -> int | None:
    try:
        from sqlglot.tokens import TokenType, Tokenizer
    except ImportError:
        return None

    tokenizer = Tokenizer(dialect="trino")
    target = _token_signature(where_expr.sql(dialect="trino"), tokenizer)
    tokens = tokenizer.tokenize(sql)
    boundaries = {
        TokenType.GROUP_BY,
        TokenType.HAVING,
        TokenType.QUALIFY,
        TokenType.ORDER_BY,
        TokenType.LIMIT,
        TokenType.UNION,
        TokenType.EXCEPT,
        TokenType.INTERSECT,
        TokenType.WINDOW,
    }
    matches: list[int] = []
    depth = 0
    for index, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            depth += 1
            continue
        if token.token_type == TokenType.R_PAREN:
            depth = max(0, depth - 1)
            continue
        if token.token_type != TokenType.WHERE:
            continue

        start_depth = depth
        clause_tokens = [token]
        local_depth = depth
        for later in tokens[index + 1:]:
            if later.token_type == TokenType.L_PAREN:
                local_depth += 1
                clause_tokens.append(later)
                continue
            if later.token_type == TokenType.R_PAREN:
                if local_depth == start_depth:
                    break
                local_depth -= 1
                clause_tokens.append(later)
                continue
            if local_depth == start_depth and later.token_type in boundaries:
                break
            clause_tokens.append(later)

        candidate = tuple(item.text.upper() for item in clause_tokens)
        if candidate == target:
            matches.append(int(token.line))

    return matches[0] if len(matches) == 1 else None


def apply(sql: str, statements: list) -> list[Finding]:
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    findings: list[Finding] = []
    seen_sites: set[int] = set()

    for stmt in statements:
        if stmt is None:
            continue
        cte_map = {cte.alias_or_name: cte.this for cte in stmt.find_all(exp.CTE) if isinstance(cte.this, exp.Select)}
        for consumer in stmt.find_all(exp.Select):
            where = consumer.args.get("where")
            if where is None:
                continue
            if consumer.args.get("joins"):
                continue
            if any(isinstance(node, exp.Or) for node in where.this.walk()):
                continue
            consumer_source = _single_source(consumer)
            if consumer_source is None:
                continue
            consumer_source_alias = _source_alias(consumer_source)
            if not consumer_source_alias:
                continue
            producer_select = _resolve_source_select(consumer_source, cte_map, exp)
            if producer_select is None:
                continue
            producer_lineage = _resolve_producer_lineage(producer_select, cte_map, exp, remaining_hops=1)
            if producer_lineage is None:
                continue

            where_line = _find_where_line(sql, where)
            if where_line is None or where_line in seen_sites:
                continue

            predicate_refs: list[_BaseRef] = []
            for predicate in _flatten_and(where.this, exp):
                refs = _predicate_base_refs(predicate, consumer_source_alias, producer_lineage, exp)
                if refs is None:
                    predicate_refs = []
                    break
                predicate_refs.extend(refs)
            if not predicate_refs:
                continue

            aliases = {ref.source_alias for ref in predicate_refs}
            if len(aliases) != 1:
                continue

            alias = next(iter(aliases))
            columns: list[str] = []
            for ref in predicate_refs:
                if ref.column not in columns:
                    columns.append(ref.column)
            if not columns:
                continue

            if len(columns) == 1:
                message = f"Filter on {alias}.{columns[0]} is applied after a joined CTE/derived table."
            else:
                joined_columns = ", ".join(f"{alias}.{column}" for column in columns)
                message = f"Filters on {joined_columns} are applied after a joined CTE/derived table."

            findings.append(
                Finding(
                    severity="medium",
                    rule_id=RULE_JOIN_FIRST_FILTER_LATE,
                    message=message,
                    suggestion="Pre-filter the relevant input before the join when semantics permit; verify equivalence manually.",
                    line=where_line,
                )
            )
            seen_sites.add(where_line)

    return findings
