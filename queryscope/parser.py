"""Parse a SQL query into its logical execution order, with findings.

Logical order (the order engines conceptually evaluate a query) is not the
written order: SELECT is written first but evaluated near the end. This
module walks the sqlglot AST and emits one Step per clause in evaluation
order, plus a list of Findings for patterns that tend to be expensive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

MAX_FRAGMENT = 140

# Nodes that do not "wrap" a column for the non-sargable check: boolean
# connectives, comparisons, and grouping constructs are transparent.
_TRANSPARENT = (
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT,
    exp.LTE, exp.Like, exp.ILike, exp.In, exp.Between, exp.Is, exp.Paren,
    exp.Predicate,
)


@dataclass
class Step:
    kind: str        # FROM, JOIN, WHERE, GROUP BY, HAVING, SELECT, ORDER BY, LIMIT, WITH
    title: str       # short label for the flow diagram
    detail: str      # the SQL fragment for this step
    note: str        # plain-English explanation


@dataclass
class Finding:
    severity: str    # "warn" or "info"
    title: str
    detail: str


@dataclass
class QueryPlan:
    sql: str
    dialect: str | None
    steps: list[Step] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # (step_index, written SQL line) pairs, in written order, for rendering.
    written_lines: list[tuple[int, str]] = field(default_factory=list)


class QueryScopeError(ValueError):
    """Raised when the SQL cannot be parsed or is not a SELECT."""


def _frag(node: exp.Expression | None, dialect: str | None) -> str:
    if node is None:
        return ""
    text = node.sql(dialect=dialect)
    if len(text) > MAX_FRAGMENT:
        text = text[: MAX_FRAGMENT - 3].rstrip() + "..."
    return text


def _table_label(node: exp.Expression, dialect: str | None) -> str:
    if isinstance(node, exp.Table):
        return node.name or _frag(node, dialect)
    return "subquery"


def _wrapped_columns(condition: exp.Expression) -> list[str]:
    """Columns that sit inside a function call in a WHERE clause.

    YEAR(created_at) = 2026 style predicates cannot use a plain index
    range scan on created_at; the engine must compute the function per row.
    """
    wrapped: list[str] = []

    def walk(node: exp.Expression, inside_func: bool) -> None:
        if isinstance(node, exp.Column):
            if inside_func and node.sql() not in wrapped:
                wrapped.append(node.sql())
            return
        child_inside = inside_func
        if isinstance(node, exp.Func) and not isinstance(node, _TRANSPARENT):
            child_inside = True
        for child in node.iter_expressions():
            walk(child, child_inside)

    walk(condition, False)
    return wrapped


def _expression_facts(
    expr: exp.Expression, dialect: str | None
) -> tuple[list[tuple[str, str, bool]], list[str], bool]:
    """Classify what lives inside one select expression.

    Returns (plain aggregates, window functions, has scalar subquery).
    Aggregates inside a window (RANK() OVER ..., SUM() OVER ...) are not
    plain aggregates: they run after grouping, over partitions. Aggregates
    inside a scalar subquery belong to that subquery, not to this query —
    counting them as this query's aggregates mislabels the trace.
    """
    plain: list[tuple[str, str, bool]] = []
    windowed: list[str] = []
    scalar = False

    def walk(node: exp.Expression, in_window: bool) -> None:
        nonlocal scalar
        if isinstance(node, exp.Select):
            scalar = True
            return
        if isinstance(node, exp.Window):
            inner = node.this
            if isinstance(inner, exp.Func):
                windowed.append(type(inner).__name__.upper())
            for child in node.iter_expressions():
                walk(child, True)
            return
        if isinstance(node, exp.AggFunc):
            if in_window:
                windowed.append(type(node).__name__.upper())
            else:
                is_distinct = bool(node.args.get("distinct")) or \
                    node.find(exp.Distinct) is not None
                plain.append((type(node).__name__.upper(),
                              node.sql(dialect=dialect), is_distinct))
            return
        for child in node.iter_expressions():
            walk(child, in_window)

    walk(expr, False)
    return plain, windowed, scalar


def _is_wildcard(expr: exp.Expression) -> bool:
    core = expr.this if isinstance(expr, exp.Alias) else expr
    return isinstance(core, exp.Star) or (
        isinstance(core, exp.Column)
        and isinstance(core.this, exp.Star))


def _scan_cte(cte: exp.CTE, plan: QueryPlan, dialect: str | None) -> None:
    """Scan a CTE body for the same expensive patterns as the main query.

    CTEs get no step-by-step trace in v1, but their worst patterns should
    not be invisible just because they live one level down.
    """
    body = cte.this
    if not isinstance(body, exp.Select):
        return
    name = cte.alias_or_name
    where = body.args.get("where")
    if where is not None:
        for col in _wrapped_columns(where.this):
            plan.findings.append(Finding(
                "warn", "Function wrapped around a filter column",
                f"Inside CTE '{name}': {col} is wrapped in a function "
                "inside WHERE, so the engine computes it per row and "
                "cannot range-scan the column. A start/end range rewrite "
                "is usually far cheaper."))
    for e in body.expressions:
        if _is_wildcard(e):
            plan.findings.append(Finding(
                "warn", "Wildcard select",
                f"Inside CTE '{name}': a wildcard select drags every "
                "column into the next step, including columns nobody "
                "reads downstream."))
        plain, windowed, _scalar = _expression_facts(e, dialect)
        for _agg_name, agg_sql, is_distinct in plain:
            if is_distinct:
                plan.findings.append(Finding(
                    "warn", "DISTINCT aggregate",
                    f"Inside CTE '{name}': {agg_sql} forces its own "
                    "distinct pass over the grouped rows before it can "
                    "aggregate."))
        if windowed:
            plan.findings.append(Finding(
                "info", "Window functions",
                f"Inside CTE '{name}': "
                f"{', '.join(sorted(set(windowed)))} run as window "
                "functions, computed after that query's grouping over "
                "partitions of its rows. Each partition and sort "
                "combination is another pass over the data."))


def parse_query(sql: str, dialect: str | None = None) -> QueryPlan:
    try:
        root = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError as err:
        raise QueryScopeError(f"Could not parse SQL: {err}") from err
    if root is None:
        raise QueryScopeError("Empty query.")

    plan = QueryPlan(sql=sql, dialect=dialect)
    query = root

    if isinstance(root, exp.Union):
        plan.findings.append(Finding(
            "info", "Set operation",
            "This query combines branches with a set operation (UNION). "
            "QueryScope traces the first branch; every branch must return "
            "matching columns."))
        query = root.this

    if not isinstance(query, exp.Select):
        raise QueryScopeError(
            f"QueryScope traces SELECT queries; got {type(root).__name__}.")

    steps: list[Step] = []
    line_for_step: dict[int, list[str]] = {}

    def add_step(step: Step, written: list[str]) -> int:
        idx = len(steps)
        steps.append(step)
        line_for_step[idx] = written
        return idx

    # CTEs are defined first.
    ctes = query.ctes
    if ctes:
        names = ", ".join(c.alias_or_name for c in ctes)
        idx = add_step(Step(
            "WITH", "WITH — CTEs", f"WITH {names}",
            "Common table expressions are defined first. The main query "
            "treats each one like a named, pre-built result set."),
            [f"WITH {names}"])
        line_for_step[idx] = [
            "WITH " + ", ".join(
                f"{c.alias_or_name} AS ({_frag(c.this, dialect)})"
                for c in ctes)]
        for c in ctes:
            _scan_cte(c, plan, dialect)

    # FROM — the engine starts here. (sqlglot >= 27 stores it as "from_".)
    from_clause = query.args.get("from_") or query.args.get("from")
    base = from_clause.this if from_clause is not None else None
    base_label = _table_label(base, dialect) if base is not None else ""
    from_idx = add_step(Step(
        "FROM", f"FROM {base_label}", _frag(from_clause, dialect),
        "The engine starts here, not at SELECT. Every later step works on "
        f"the rows that {base_label or 'the source'} produces."),
        [_frag(from_clause, dialect)])
    if isinstance(base, (exp.Subquery, exp.Select)):
        plan.findings.append(Finding(
            "info", "Subquery in FROM",
            "The FROM source is a subquery. It runs first and QueryScope "
            "traces it as a black box; its internal steps are not "
            "broken out in this version."))

    # JOINs, in written order.
    joins = query.args.get("joins") or []
    for join in joins:
        target = join.this
        label = _table_label(target, dialect)
        side = (join.args.get("side") or "").upper()
        kind = (join.args.get("kind") or "").upper()
        join_word = " ".join(w for w in [side, kind, "JOIN"] if w)
        on = join.args.get("on")
        if on is None:
            plan.findings.append(Finding(
                "warn", "Join with no condition",
                f"The join to {label} has no ON condition, so every row "
                "pairs with every row on the other side (a cartesian "
                "product). Row counts can explode here."))
            note = (f"A {join_word.lower()} to {label} with no condition: "
                    "every row pairs with every row. Dangerous.")
        else:
            note = (f"Each row picks up columns from {label} where "
                    f"{_frag(on, dialect)}. Rows multiply if the join key "
                    f"is not unique on the {label} side.")
        add_step(Step("JOIN", f"{join_word} {label}", _frag(join, dialect),
                      note),
                 [_frag(join, dialect)])

    # WHERE.
    where = query.args.get("where")
    where_idx: int | None = None
    if where is not None:
        condition = where.this
        wrapped = _wrapped_columns(condition)
        for col in wrapped:
            plan.findings.append(Finding(
                "warn", "Function wrapped around a filter column",
                f"{col} is wrapped in a function inside WHERE. The engine "
                "must compute that function for every row before it can "
                "filter, which defeats index range scans on the column. "
                "Rewriting it as a range (for example a start and end "
                "date) is usually far cheaper."))
        where_idx = add_step(Step(
            "WHERE", "WHERE", _frag(where, dialect),
            "Rows are filtered here, before any grouping happens. "
            f"Predicates: {_frag(condition, dialect)}."),
            [_frag(where, dialect)])

    if where is not None and joins:
        plan.findings.append(Finding(
            "info", "Filters are evaluated after the joins",
            "In logical order, WHERE runs after the joins, so the joins "
            "conceptually carry unfiltered rows. Most optimizers push "
            "filters down automatically, but stacked views and some "
            "engines do not — worth knowing when a join feels slow."))

    # GROUP BY + SELECT (aggregates are computed at grouping time), or a
    # plain SELECT step when there is no grouping.
    group = query.args.get("group")
    expressions = query.expressions
    select_text = "SELECT " + ", ".join(
        _frag(e, dialect) for e in expressions)
    distinct = query.args.get("distinct") is not None

    agg_names: list[str] = []
    window_funcs: list[str] = []
    has_scalar_subquery = False
    for e in expressions:
        plain, windowed, scalar = _expression_facts(e, dialect)
        has_scalar_subquery = has_scalar_subquery or scalar
        window_funcs.extend(windowed)
        for agg_name, agg_sql, is_distinct in plain:
            agg_names.append(agg_name)
            if is_distinct:
                plan.findings.append(Finding(
                    "warn", "DISTINCT aggregate",
                    f"{agg_sql} forces its own distinct pass over the "
                    "grouped rows before it can aggregate. On wide data "
                    "this is often the single most expensive expression "
                    "in the query."))
        # Wildcard check: the select target itself is a star (or t.*).
        # A star inside COUNT(*) is an aggregate argument, not a wildcard.
        if _is_wildcard(e):
            plan.findings.append(Finding(
                "warn", "Wildcard select",
                "SELECT * drags every column through every step of the "
                "query, including columns nobody reads. Naming the "
                "columns you need is cheaper and survives schema changes."))
    if window_funcs:
        plan.findings.append(Finding(
            "info", "Window functions",
            f"{', '.join(sorted(set(window_funcs)))} run as window "
            "functions: computed after grouping, over partitions of the "
            "result rows. Every distinct partition and sort combination "
            "costs its own pass, and a filter on a window result (like "
            "a rank) can only be applied after the full window "
            "computation finishes."))
    if has_scalar_subquery:
        plan.findings.append(Finding(
            "info", "Scalar subquery in SELECT",
            "A (SELECT ...) sits in the select list. It is its own "
            "query with its own scan of its source. Engines usually "
            "evaluate a constant scalar subquery once, but it hides "
            "real work inside the SELECT list where nobody costs it."))

    if group is not None:
        group_cols = ", ".join(_frag(e, dialect)
                                for e in group.expressions)
        aggs = f" Aggregates computed here: {', '.join(sorted(set(agg_names)))}." if agg_names else ""
        step_idx = add_step(Step(
            "GROUP BY", "GROUP BY + SELECT",
            f"GROUP BY {group_cols}",
            "Rows collapse into one output row per unique combination of "
            f"{group_cols}. This is also where SELECT is evaluated: the "
            f"output columns are chosen and computed now.{aggs}"),
            [select_text, f"GROUP BY {group_cols}"])
        line_for_step[step_idx] = [select_text, f"GROUP BY {group_cols}"]
    else:
        note = ("Output columns are chosen and computed here. Anything "
                "written above this line in the query has already run.")
        if distinct:
            note += (" DISTINCT is on: the engine must dedupe the full "
                     "result, an extra pass over every output row.")
            plan.findings.append(Finding(
                "info", "DISTINCT dedupe pass",
                "SELECT DISTINCT forces the engine to sort or hash the "
                "entire result to remove duplicates before returning it."))
        add_step(Step("SELECT", "SELECT", select_text, note), [select_text])

    # HAVING.
    having = query.args.get("having")
    if having is not None:
        add_step(Step(
            "HAVING", "HAVING", _frag(having, dialect),
            "Filters the groups themselves, after aggregation. This is "
            "the only place conditions on aggregate values can go."),
            [_frag(having, dialect)])

    # ORDER BY.
    order = query.args.get("order")
    limit = query.args.get("limit")
    if order is not None:
        note = ("The final rows are sorted here. Sorting is cheap once "
                "grouping has shrunk the data, and expensive on raw "
                "detail rows.")
        if limit is None:
            note += " There is no LIMIT, so the whole result is sorted."
            plan.findings.append(Finding(
                "info", "Sort with no LIMIT",
                "ORDER BY without LIMIT sorts the entire result set. "
                "If only the top rows matter, a LIMIT lets the engine "
                "use a much cheaper top-N sort."))
        add_step(Step("ORDER BY", "ORDER BY", _frag(order, dialect), note),
                 [_frag(order, dialect)])

    # LIMIT.
    if limit is not None:
        add_step(Step(
            "LIMIT", "LIMIT", _frag(limit, dialect),
            "Only the first rows after sorting are kept. Everything "
            "upstream still ran."),
            [_frag(limit, dialect)])

    if where is None and group is not None:
        plan.findings.append(Finding(
            "info", "No row filter before grouping",
            "There is no WHERE clause, so every row the joins produce "
            "flows into the grouping step."))

    plan.steps = steps
    # Written order: WITH first (already first if present), then SELECT,
    # FROM, JOINs, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT. line_for_step
    # is keyed by execution step index; rebuild written order explicitly.
    plan.written_lines = _written_order(plan, query, dialect,
                                        from_idx, where_idx, line_for_step)
    return plan


def _written_order(plan: QueryPlan, query: exp.Select,
                   dialect: str | None, from_idx: int,
                   where_idx: int | None,
                   line_for_step: dict[int, list[str]]) -> list[tuple[int, str]]:
    """Rebuild (step_index, line) pairs in the order SQL is written."""
    kind_to_idx: dict[str, int] = {}
    for i, s in enumerate(plan.steps):
        kind_to_idx.setdefault(s.kind, i)
    lines: list[tuple[int, str]] = []
    if "WITH" in kind_to_idx:
        lines += [(kind_to_idx["WITH"], t)
                  for t in line_for_step[kind_to_idx["WITH"]]]
    select_idx = kind_to_idx.get("SELECT", kind_to_idx.get("GROUP BY"))
    if select_idx is not None:
        lines.append((select_idx, line_for_step[select_idx][0]))
    lines.append((from_idx, plan.steps[from_idx].detail))
    join_indices = [i for i, s in enumerate(plan.steps) if s.kind == "JOIN"]
    for ji in join_indices:
        lines.append((ji, plan.steps[ji].detail))
    if where_idx is not None:
        lines.append((where_idx, plan.steps[where_idx].detail))
    if "GROUP BY" in kind_to_idx:
        gi = kind_to_idx["GROUP BY"]
        if len(line_for_step[gi]) > 1:
            lines.append((gi, line_for_step[gi][1]))
    for kind in ("HAVING", "ORDER BY", "LIMIT"):
        if kind in kind_to_idx:
            i = kind_to_idx[kind]
            lines.append((i, plan.steps[i].detail))
    return lines
