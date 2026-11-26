from .conftest import auth, insert_row, register, token_for


def seed(db, cid):
    rows = [
        # created_at,                category,   label,      score,  tags
        ("2026-03-02T10:15:00.000Z", "billing",  "negative", -0.6,  ["refund", "urgent"]),   # Mon
        ("2026-03-02T11:45:00.000Z", "billing",  "negative", -0.4,  ["refund"]),
        ("2026-03-03T09:00:00.000Z", "shipping", "positive",  0.7,  []),                     # Tue
        ("2026-03-03T09:30:00.000Z", "shipping", "neutral",   0.0,  []),
        ("2026-03-08T23:59:00.000Z", "support",  "positive",  0.5,  ["feature-request"]),    # Sun, same ISO week
        ("2026-03-09T00:00:00.000Z", "support",  "negative", -0.2,  ["churn"]),              # next Mon
    ]
    for ts, cat, label, score, tags in rows:
        insert_row(db, cid, created_at=ts, category=cat, label=label, score=score, tags=tags)


def test_summary_counts_categories_buckets_and_tags(client, tenant, db):
    seed(db, tenant["created"]["client_id"])
    r = client.get("/dashboard/summary?from=2026-03-01&to=2026-03-10&bucket=day", headers=tenant["headers"])
    assert r.status_code == 200, r.text
    s = r.json()

    assert s["window"] == {"from": "2026-03-01T00:00:00.000Z", "to": "2026-03-10T00:00:00.000Z", "bucket": "day", "category": None}
    assert s["totals"] == {"total": 6, "positive": 2, "negative": 3, "neutral": 1, "avg_score": 0.0}

    by_cat = {c["category"]: c for c in s["by_category"]}
    assert by_cat["billing"] == {"category": "billing", "total": 2, "positive": 0, "negative": 2, "neutral": 0, "avg_score": -0.5}
    assert by_cat["shipping"]["total"] == 2 and by_cat["shipping"]["avg_score"] == 0.35
    assert [c["category"] for c in s["by_category"]][:1] == ["billing"]      # sorted by total desc, then name

    assert [t["bucket"] for t in s["timeline"]] == ["2026-03-02T00:00:00.000Z", "2026-03-03T00:00:00.000Z",
                                                    "2026-03-08T00:00:00.000Z", "2026-03-09T00:00:00.000Z"]
    assert s["timeline"][0]["negative"] == 2

    assert s["top_tags"][0] == {"tag": "refund", "count": 2}
    assert {t["tag"] for t in s["top_tags"]} == {"refund", "urgent", "feature-request", "churn"}


def test_summary_buckets_by_hour_and_iso_week(client, tenant, db):
    seed(db, tenant["created"]["client_id"])
    h = tenant["headers"]
    hours = client.get("/dashboard/summary?from=2026-03-02&to=2026-03-03&bucket=hour", headers=h).json()["timeline"]
    assert [t["bucket"] for t in hours] == ["2026-03-02T10:00:00.000Z", "2026-03-02T11:00:00.000Z"]

    weeks = client.get("/dashboard/summary?from=2026-03-01&to=2026-03-10&bucket=week", headers=h).json()["timeline"]
    assert [(t["bucket"], t["total"]) for t in weeks] == [("2026-03-02T00:00:00.000Z", 5), ("2026-03-09T00:00:00.000Z", 1)]


def test_summary_category_filter_and_defaults(client, tenant, db):
    seed(db, tenant["created"]["client_id"])
    h = tenant["headers"]
    s = client.get("/dashboard/summary?from=2026-03-01&to=2026-03-10&category=shipping", headers=h).json()
    assert s["totals"]["total"] == 2 and s["window"]["category"] == "shipping"
    assert [c["category"] for c in s["by_category"]] == ["shipping"]

    default = client.get("/dashboard/summary", headers=h).json()     # last 7 days: seeded rows are outside it
    assert default["window"]["bucket"] == "day"
    assert default["totals"]["total"] == 0


def test_summary_validates_window(client, tenant):
    h = tenant["headers"]
    assert client.get("/dashboard/summary?bucket=month", headers=h).status_code == 422
    assert client.get("/dashboard/summary?from=2026-02-01&to=2026-01-01", headers=h).status_code == 400
    assert client.get("/dashboard/summary?from=2020-01-01&to=2026-01-01", headers=h).status_code == 400


def test_summary_is_tenant_scoped(client, tenant, db):
    seed(db, tenant["created"]["client_id"])
    other = auth(token_for(client, register(client, "other", rpm=100)))
    s = client.get("/dashboard/summary?from=2026-03-01&to=2026-03-10", headers=other).json()
    assert s["totals"]["total"] == 0 and s["by_category"] == [] and s["top_tags"] == []
