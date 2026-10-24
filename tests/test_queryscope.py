import pathlib

import pytest

from queryscope.parser import QueryScopeError, parse_query
from queryscope.render import render_html

EXAMPLES = pathlib.Path(__file__).parent.parent / "examples"

RENT_SQL = (EXAMPLES / "rent_by_property.sql").read_text()
EVENT_SQL = (EXAMPLES / "event_feed.sql").read_text()
SIGNUP_SQL = (EXAMPLES / "signups_by_channel.sql").read_text()


def kinds(plan):
    return [s.kind for s in plan.steps]


def finding_titles(plan):
    return [f.title for f in plan.findings]


def test_execution_order_not_written_order():
    plan = parse_query(RENT_SQL)
    assert kinds(plan) == [
        "FROM", "JOIN", "JOIN", "WHERE", "GROUP BY", "ORDER BY"]
    # The written SQL still starts with SELECT.
    assert plan.written_lines[0][1].startswith("SELECT")
    # ...but its step index points at the GROUP BY + SELECT step (4th, 0-based 4).
    assert plan.written_lines[0][0] == 4


def test_from_comes_first():
    plan = parse_query(SIGNUP_SQL)
    assert plan.steps[0].kind == "FROM"
    assert "signups" in plan.steps[0].title


def test_distinct_aggregate_flagged():
    plan = parse_query(RENT_SQL)
    assert "DISTINCT aggregate" in finding_titles(plan)


def test_late_filter_info_present_when_joins_and_where():
    plan = parse_query(RENT_SQL)
    assert "Filters are evaluated after the joins" in finding_titles(plan)


def test_wildcard_flagged():
    plan = parse_query(EVENT_SQL)
    assert "Wildcard select" in finding_titles(plan)


def test_function_wrapped_filter_column_flagged():
    plan = parse_query(EVENT_SQL)
    assert "Function wrapped around a filter column" in finding_titles(plan)


def test_sort_without_limit_flagged():
    plan = parse_query(EVENT_SQL)
    assert "Sort with no LIMIT" in finding_titles(plan)


def test_limit_means_no_sort_finding():
    plan = parse_query(SIGNUP_SQL)
    assert "Sort with no LIMIT" not in finding_titles(plan)
    assert kinds(plan)[-1] == "LIMIT"


def test_simple_query_has_no_warnings():
    plan = parse_query(SIGNUP_SQL)
    assert all(f.severity == "info" for f in plan.findings)


def test_cartesian_join_flagged():
    plan = parse_query(
        "SELECT a.id FROM a JOIN b ON 1 = 1")
    # ON 1=1 parses as a condition; a true cross join has no ON at all.
    plan2 = parse_query("SELECT a.id FROM a CROSS JOIN b")
    assert "Join with no condition" in finding_titles(plan2)


def test_cte_gets_first_step():
    plan = parse_query(
        "WITH t AS (SELECT id FROM users) SELECT id FROM t")
    assert plan.steps[0].kind == "WITH"
    assert plan.steps[1].kind == "FROM"


def test_plain_select_step_without_group_by():
    plan = parse_query("SELECT id, name FROM users WHERE id > 10")
    assert kinds(plan) == ["FROM", "WHERE", "SELECT"]


def test_invalid_sql_raises():
    with pytest.raises(QueryScopeError):
        parse_query("SELECT FROM WHERE")


def test_non_select_raises():
    with pytest.raises(QueryScopeError):
        parse_query("DELETE FROM users WHERE id = 1")


def test_render_contains_steps_and_sql():
    plan = parse_query(RENT_SQL)
    page = render_html(plan)
    assert "QueryScope" in page
    assert "fact_leases" in page
    assert page.count('class="step"') == len(plan.steps)
    assert "Play execution" in page


TOP_SQL = (EXAMPLES / "top_properties.sql").read_text()


def test_cte_bodies_are_scanned_for_findings():
    plan = parse_query(TOP_SQL)
    details = " ".join(f.detail for f in plan.findings)
    # The YEAR() wrapper and COUNT(DISTINCT) live inside the monthly CTE.
    assert "Inside CTE 'monthly'" in details
    assert "Function wrapped around a filter column" in finding_titles(plan)
    assert "DISTINCT aggregate" in finding_titles(plan)


def test_window_functions_detected_in_cte():
    plan = parse_query(TOP_SQL)
    details = " ".join(f.detail for f in plan.findings)
    assert "Window functions" in finding_titles(plan)
    assert "ranked" in details


def test_window_functions_in_main_query():
    plan = parse_query(
        "SELECT id, RANK() OVER (ORDER BY score DESC) AS r FROM t")
    assert "Window functions" in finding_titles(plan)


def test_scalar_subquery_not_counted_as_outer_aggregate():
    plan = parse_query(TOP_SQL)
    assert "Scalar subquery in SELECT" in finding_titles(plan)
    select_step = [s for s in plan.steps if s.kind == "GROUP BY"][0]
    # MAX belongs to the scalar subquery, not to the outer query.
    assert "MAX" not in select_step.note


def test_from_subquery_flagged():
    plan = parse_query(
        "SELECT id FROM (SELECT id FROM users) s")
    assert "Subquery in FROM" in finding_titles(plan)
