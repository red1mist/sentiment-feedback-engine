"""Print SQLite's query plans for the list and dashboard queries.

    python -m app.explain [--seed] [DATABASE_PATH]

Shows that every query is answered from one of the compound indexes (SEARCH
... USING INDEX) with no full scan and no temp b-tree for ORDER BY. --seed
inserts a few rows into a temporary database first, so it works in CI.
"""

from __future__ import annotations

import os
import sys
import tempfile

from .classifier import Sentiment
from .db import connect, migrate
from .repos import ClientRepo, FeedbackRepo


def plan(conn, sql: str, params: list) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(r["detail"] for r in rows)


def seed(conn) -> str:
    client = ClientRepo(conn).create("explain", 60)
    repo = FeedbackRepo(conn)
    for i, (cat, label, score) in enumerate([("billing", "negative", -0.6), ("shipping", "positive", 0.7),
                                             ("billing", "neutral", 0.0), ("support", "positive", 0.5)]):
        repo.insert(client_id=client["client_id"], text=f"sample {i}", source="seed", category=cat,
                    tags=["refund"] if cat == "billing" else [], sentiment=Sentiment(label, score, abs(score), "seed"))
    return client["client_id"]


def main(argv: list[str]) -> int:
    do_seed = "--seed" in argv
    args = [a for a in argv if a != "--seed"]
    path = args[0] if args else os.environ.get("DATABASE_PATH", "feedback.db")
    if do_seed:
        path = os.path.join(tempfile.mkdtemp(), "explain.db")
    conn = connect(path)
    migrate(conn)
    client_id = seed(conn) if do_seed else (conn.execute("SELECT client_id FROM feedback LIMIT 1").fetchone() or ["ck_none"])[0]
    from_, to = "2026-01-01T00:00:00.000Z", "2026-12-31T00:00:00.000Z"

    sql, p = FeedbackRepo.list_query(client_id, category="billing", limit=20)
    print("GET /feedback?category=billing            ", plan(conn, sql, p))
    sql, p = FeedbackRepo.list_query(client_id, sentiment="negative", from_=from_, to=to, limit=20)
    print("GET /feedback?sentiment=negative&from&to  ", plan(conn, sql, p))
    sql, p = FeedbackRepo.list_query(client_id, cursor=(to, 0), limit=20)
    print("GET /feedback?cursor=...                  ", plan(conn, sql, p))
    for name, (sql, p) in FeedbackRepo.summary_queries(client_id, from_=from_, to=to, bucket="day").items():
        print(f"GET /dashboard/summary  [{name:<11}] ", plan(conn, sql, p))
    sql, p = FeedbackRepo.summary_queries(client_id, from_=from_, to=to, bucket="day", category="billing")["totals"]
    print("GET /dashboard/summary?category=billing   ", plan(conn, sql, p))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
