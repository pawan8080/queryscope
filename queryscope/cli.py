"""Command line interface for QueryScope.

Usage:
    python -m queryscope query.sql [--dialect snowflake] [--out report.html]
    python -m queryscope --sql "SELECT ..." [--out report.html]

Prints the execution-order trace to stdout and writes the HTML animation.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from queryscope.parser import QueryScopeError, parse_query
from queryscope.render import render_html


def _trace_text(plan) -> str:
    lines = ["Logical execution order (not the order it is written in):", ""]
    for i, step in enumerate(plan.steps, start=1):
        lines.append(f"{i}. {step.title}")
        lines.append(f"   {step.detail}")
        lines.append(f"   {step.note}")
        lines.append("")
    if plan.findings:
        lines.append(f"Findings ({len(plan.findings)}):")
        for f in plan.findings:
            lines.append(f"  [{f.severity.upper()}] {f.title} — {f.detail}")
    else:
        lines.append("Findings: none. No expensive patterns spotted.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="queryscope",
        description="Replay a SQL query in the order the engine "
                    "actually executes it.")
    parser.add_argument("file", nargs="?",
                        help="Path to a .sql file (or - for stdin).")
    parser.add_argument("--sql", help="SQL text passed directly.")
    parser.add_argument("--dialect",
                        help="sqlglot dialect, e.g. postgres, snowflake, "
                             "bigquery, duckdb, mysql, tsql.")
    parser.add_argument("--out", help="Where to write the HTML report. "
                                      "Default: <input name>.html next to "
                                      "the input, or queryscope.html.")
    args = parser.parse_args(argv)

    if args.sql:
        sql = args.sql
        default_out = "queryscope.html"
    elif args.file:
        if args.file == "-":
            sql = sys.stdin.read()
            default_out = "queryscope.html"
        else:
            sql = pathlib.Path(args.file).read_text(encoding="utf-8")
            default_out = str(pathlib.Path(args.file).with_suffix(".html"))
    else:
        parser.error("Give a .sql file or --sql \"...\"")

    try:
        plan = parse_query(sql, dialect=args.dialect)
    except QueryScopeError as err:
        print(f"queryscope: {err}", file=sys.stderr)
        return 2

    print(_trace_text(plan))
    out_path = pathlib.Path(args.out or default_out)
    out_path.write_text(render_html(plan), encoding="utf-8")
    print(f"\nHTML report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
