"""The indexes are not decoration: SQLite must actually use them.

EXPLAIN QUERY PLAN for every query the API issues has to show an index SEARCH
on the feedback table, never a full SCAN, and the list queries must not need a
temp b-tree for their ORDER BY (the index already delivers the order)."""

import pytest

from app.repos import FeedbackRepo

from .conftest import insert_row

FROM, TO = "2026-01-01T00:00:00.000Z", "2026-12-31T00:00:00.000Z"


@pytest.fixture
def seeded(db):
    db.execute("INSERT INTO clients (client_id, name, secret_hash, rate_limit_per_minute, created_at) VALUES ('ck_1','n','x',1,'t')")
    for i in range(20):
        insert_row(db, "ck_1", created_at=f"2026-06-{(i % 28) + 1:02d}T12:00:00.000Z", category="billing" if i % 2 else "shipping",
                   label="negative" if i % 3 else "positive", tags=["refund"] if i % 2 else [])
    db.execute("ANALYZE")
    return db


def plan(db, sql, params):
    return " | ".join(r["detail"] for r in db.execute("EXPLAIN QUERY PLAN " + sql, params))


@pytest.mark.parametrize("filters,indexes", [
    ({}, ("feedback_client_time",)),
    ({"category": "billing"}, ("feedback_client_category_time",)),
    ({"sentiment": "negative", "from_": FROM, "to": TO}, ("feedback_client_sentiment_time",)),
    ({"cursor": (TO, 0)}, ("feedback_client_time",)),
    # With an equality, a range and a cursor the planner may seek on either the
    # category index or the time range and filter the rest; both are fine.
    ({"category": "billing", "from_": FROM, "to": TO, "cursor": (TO, 0)},
     ("feedback_client_category_time", "feedback_client_time")),
])
def test_list_queries_use_the_matching_index_without_sorting(seeded, filters, indexes):
    sql, params = FeedbackRepo.list_query("ck_1", limit=20, **filters)
    p = plan(seeded, sql, params)
    assert any(f"SEARCH feedback USING INDEX {ix}" in p for ix in indexes), p
    assert "SCAN feedback" not in p
    assert "TEMP B-TREE FOR ORDER BY" not in p, p


@pytest.mark.parametrize("category", [None, "billing"])
def test_dashboard_queries_are_index_backed(seeded, category):
    for name, (sql, params) in FeedbackRepo.summary_queries("ck_1", from_=FROM, to=TO, bucket="day", category=category).items():
        p = plan(seeded, sql, params)
        assert "INDEX feedback_client_" in p, (name, p)
        assert "SCAN feedback" not in p and "SCAN f " not in p, (name, p)
