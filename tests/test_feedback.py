from .conftest import auth, register, token_for


def test_create_classifies_tags_and_stores(client, tenant, db):
    r = client.post("/feedback", headers=tenant["headers"],
                    json={"text": "Frustrating: I was charged twice and my refund request was ignored. Urgent!",
                          "source": "web", "metadata": {"order_id": "o-1", "vip": True}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sentiment"]["label"] == "negative"
    assert body["sentiment"]["model"] == "vader-3.3.2"
    assert body["category"] == "billing"
    assert sorted(body["tags"]) == ["refund", "urgent"]
    assert body["source"] == "web"
    assert body["metadata"] == {"order_id": "o-1", "vip": True}
    assert body["created_at"].endswith("Z")
    assert "client_id" not in body

    row = db.execute("SELECT client_id, sentiment_label FROM feedback WHERE id = ?", (body["id"],)).fetchone()
    assert row["client_id"] == tenant["created"]["client_id"]
    assert row["sentiment_label"] == "negative"
    assert client.get("/metrics").json()["classified"] == 1


def test_create_validates_body(client, tenant):
    h = tenant["headers"]
    assert client.post("/feedback", headers=h, json={}).status_code == 422
    assert client.post("/feedback", headers=h, json={"text": "   "}).status_code == 422
    assert client.post("/feedback", headers=h, json={"text": "x" * 5001}).status_code == 422
    assert client.post("/feedback", headers=h, json={"text": "ok", "metadata": {"nested": {"a": 1}}}).status_code == 422
    r = client.post("/feedback", headers={**h, "content-type": "application/json"}, content="{bad")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_preview_does_not_store(client, tenant, db):
    r = client.post("/feedback/classify", headers=tenant["headers"], json={"text": "Terrible: charged twice and no refund"})
    assert r.status_code == 200
    assert r.json()["sentiment"]["label"] == "negative"
    assert r.json()["category"] == "billing"
    assert r.json()["tags"] == ["refund"]
    assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_get_is_scoped_to_the_calling_client(client, tenant):
    created = client.post("/feedback", headers=tenant["headers"], json={"text": "ok"}).json()
    assert client.get(f"/feedback/{created['id']}", headers=tenant["headers"]).status_code == 200
    other = auth(token_for(client, register(client, "other", rpm=100)))
    assert client.get(f"/feedback/{created['id']}", headers=other).status_code == 404
    assert client.get("/feedback/999999", headers=tenant["headers"]).status_code == 404
    assert client.get("/feedback/not-an-id", headers=tenant["headers"]).status_code == 422


def test_list_filters(client, tenant):
    h = tenant["headers"]
    for text in ["Fast shipping, great quality!", "Terrible: charged twice, refund now", "Package arrived on Tuesday."]:
        assert client.post("/feedback", headers=h, json={"text": text}).status_code == 201

    assert len(client.get("/feedback", headers=h).json()["items"]) == 3
    neg = client.get("/feedback?sentiment=negative", headers=h).json()["items"]
    assert [i["category"] for i in neg] == ["billing"]
    assert len(client.get("/feedback?category=shipping", headers=h).json()["items"]) == 2
    assert len(client.get("/feedback?category=shipping&sentiment=positive", headers=h).json()["items"]) == 1
    assert client.get("/feedback?from=2030-01-01", headers=h).json()["items"] == []
    assert len(client.get("/feedback?from=2020-01-01&to=2030-01-01", headers=h).json()["items"]) == 3

    assert client.get("/feedback?category=nope", headers=h).status_code == 422
    assert client.get("/feedback?limit=1000", headers=h).status_code == 422
    assert client.get("/feedback?from=2026-02-01&to=2026-01-01", headers=h).status_code == 400
    assert client.get("/feedback?cursor=!!!", headers=h).status_code == 400


def test_keyset_pagination_is_stable_with_identical_timestamps(client, tenant, db):
    cid = tenant["created"]["client_id"]
    from .conftest import insert_row
    # five rows: three share a timestamp, so the id tiebreaker has to do the work
    for i, ts in enumerate(["2026-03-01T10:00:00.000Z", "2026-03-01T10:00:00.000Z", "2026-03-01T10:00:00.000Z",
                            "2026-03-01T09:00:00.000Z", "2026-02-28T10:00:00.000Z"]):
        insert_row(db, cid, created_at=ts, text=f"row {i}")

    seen, cursor, pages = [], None, 0
    while True:
        url = "/feedback?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=tenant["headers"]).json()
        seen += [(i["created_at"], i["id"]) for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert len(seen) == 5 and len(set(seen)) == 5
    assert seen == sorted(seen, reverse=True)      # created_at desc, id desc, no gaps or repeats


def test_tenant_isolation_in_list(client, tenant):
    client.post("/feedback", headers=tenant["headers"], json={"text": "mine"})
    other = auth(token_for(client, register(client, "other", rpm=100)))
    assert client.get("/feedback", headers=other).json()["items"] == []
