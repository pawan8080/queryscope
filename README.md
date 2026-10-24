# QueryScope

**Watch your SQL run in the order the engine actually executes it.**

Everyone reads a query top to bottom: SELECT first, then FROM, then WHERE.
That is not the order any engine runs it in. The engine starts at FROM,
joins, filters, groups — and only near the end evaluates the SELECT list
everyone stared at first. That gap between written order and real order is
where slow queries hide: filters that conceptually arrive after expensive
joins, `COUNT(DISTINCT ...)` expressions nobody costed, sorts over millions
of unsorted detail rows.

QueryScope takes any SQL query, rebuilds its **logical execution order**,
explains each step in plain English, flags the expensive patterns, and
renders a self-contained HTML page that animates the query executing,
step by step.

**Live demo:** [pawan8080.github.io/queryscope](https://pawan8080.github.io/queryscope/) — four example queries, animated in the browser. Press play.

## Quick start

```bash
pip install -r requirements.txt

python -m queryscope examples/rent_by_property.sql
# -> prints the step-by-step trace, writes examples/rent_by_property.html

python -m queryscope --sql "SELECT channel, COUNT(*) FROM signups GROUP BY channel" --dialect duckdb
```

Open the HTML, press **Play execution**, and watch each clause light up in
the SQL and in the flow diagram while the note under it explains what the
engine is doing at that step.

## What it catches

- **DISTINCT aggregates** — `COUNT(DISTINCT x)` forces its own distinct
  pass over the grouped rows; often the priciest expression in the query.
- **Function-wrapped filter columns** — `WHERE YEAR(created_at) = 2026`
  defeats index range scans; the engine computes the function per row.
- **Wildcard selects** — `SELECT *` drags every column through every step.
- **Joins with no condition** — cartesian products waiting to happen.
- **Filters evaluated after joins** — the logical-order trap behind a lot
  of "why is this join slow" mysteries.
- **Sorts with no LIMIT** — full-result sorts where a top-N would do.
- **No filter before grouping** — every joined row flows into GROUP BY.

## How it works

QueryScope parses the query with [sqlglot](https://github.com/tobymao/sqlglot)
into an AST, then walks the AST in logical evaluation order:

```
FROM -> JOIN -> WHERE -> GROUP BY -> HAVING -> SELECT -> ORDER BY -> LIMIT
```

Each clause becomes one step with its SQL fragment and a generated
explanation. Findings are structural checks over the same AST: aggregate
nodes containing a DISTINCT, columns wrapped in functions inside WHERE,
star expressions, condition-less joins, and clause combinations like
ORDER BY without LIMIT.

Dialects: anything sqlglot speaks — `--dialect postgres`, `snowflake`,
`bigquery`, `duckdb`, `mysql`, `tsql`, and more.

## Honest limits

- This is **logical** execution order — the conceptual model every
  engine's documentation teaches. A real optimizer may reorder operations
  (predicate pushdown, join reordering) when it can prove the result is
  unchanged.
- QueryScope never connects to a database. It reads query structure
  only, so it cannot show real row counts — those depend on your data.
  Run it next to `EXPLAIN`, not instead of it.

## Examples

| File | Shows |
| --- | --- |
| `examples/rent_by_property.sql` | Joins, a late filter, a `COUNT(DISTINCT)` in the SELECT |
| `examples/event_feed.sql` | Wildcard select, function-wrapped filter, sort with no LIMIT |
| `examples/signups_by_channel.sql` | The clean case — QueryScope stays quiet |
| `examples/top_properties.sql` | The stress test: CTEs, window functions, a scalar subquery, a redundant outer GROUP BY |

## Tests

```bash
python -m pytest tests/ -q
```

## License

MIT
