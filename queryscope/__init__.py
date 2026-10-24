"""QueryScope: show a SQL query in the order the engine actually runs it.

Parses SQL with sqlglot, rebuilds the logical execution order
(FROM -> JOIN -> WHERE -> GROUP BY -> HAVING -> SELECT -> ORDER BY -> LIMIT),
explains each step in plain English, flags expensive patterns, and renders
a self-contained HTML animation of the query executing.
"""

from queryscope.parser import Finding, QueryPlan, Step, parse_query

__all__ = ["Finding", "QueryPlan", "Step", "parse_query"]
__version__ = "0.1.0"
