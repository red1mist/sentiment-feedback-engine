# Sentiment-Driven Feedback Engine

A FastAPI service that takes raw customer feedback, classifies its sentiment in
real time, auto-tags it by category, stores it in SQLite and serves dashboard
aggregations over it. Plain Python: one process, one database file, nothing to
install besides the requirements.

**Stack:** Python 3.12 · FastAPI · SQLite (standard library `sqlite3`) · PyJWT · VADER

```
                 ┌──────────────────────────────────────────────────────────────┐
  client ──JWT──►│ FastAPI                                                      │
                 │  auth deps ──► rate limit ──► route                          │
                 │                                 │                            │
                 │        ┌────────────────────────┼───────────────────┐        │
                 │        ▼                        ▼                   ▼        │
                 │   classifier              tagger               repos (SQL)   │
                 │   VADER / HF model        category + tags      keyset list,  │
                 │   label, score, conf.                          GROUP BY      │
                 └──────────────────────────────────────┬───────────────────────┘
                                                        ▼
                                                 feedback.db (SQLite, WAL)
                                                 clients, feedback + 3 compound indexes
```

## Quick start

```sh
git clone https://github.com/red1mist/sentiment-feedback-engine && cd sentiment-feedback-engine
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload        # http://127.0.0.1:8000, creates feedback.db on first start
.venv/bin/python -m pytest                      # 43 tests
```

Interactive docs are at `/docs`. Development defaults are built in; copy
`.env.example` to `.env`, change the secrets and start with `--env-file .env`
for anything beyond your own machine (`APP_ENV=production` refuses the defaults).

### A full round trip

```sh
BASE=http://127.0.0.1:8000

# 1. register an API client (admin). The secret is shown exactly once.
CLIENT=$(curl -s -X POST $BASE/auth/clients -H 'X-Admin-Key: dev-admin-key-change-me' \
  -H 'content-type: application/json' -d '{"name":"acme","rate_limit_per_minute":120}')

# 2. exchange the credentials for a JWT (1 h by default)
TOKEN=$(curl -s -X POST $BASE/auth/token -H 'content-type: application/json' \
  -d "{\"client_id\":$(jq .client_id <<<$CLIENT),\"client_secret\":$(jq .client_secret <<<$CLIENT)}" | jq -r .access_token)

# 3. submit feedback: classified, tagged and stored in one call
curl -s -X POST $BASE/feedback -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"text":"Frustrating: I was charged twice and my refund request was ignored. Urgent!","source":"web"}' | jq
```

```json
{
  "id": 1,
  "text": "Frustrating: I was charged twice and my refund request was ignored. Urgent!",
  "source": "web",
  "category": "billing",
  "tags": ["urgent", "refund"],
  "sentiment": { "label": "negative", "score": -0.6696, "confidence": 0.6696, "model": "vader-3.3.2" },
  "metadata": null,
  "created_at": "2026-11-26T09:15:30.123Z"
}
```

```sh
# 4. dashboard: counts and mean polarity per label, per category, per day, plus top tags
curl -s "$BASE/dashboard/summary?from=2026-11-01&to=2026-12-01&bucket=day" -H "Authorization: Bearer $TOKEN" | jq
```

## API

All responses are JSON. Errors share one shape:
`{"error": {"code": "...", "message": "...", "details": [...]}}`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | 200 if the database answers, else 503 |
| `GET` | `/metrics` | none | classifier call count and latency |
| `POST` | `/auth/clients` | `X-Admin-Key` | register a client; body `{name, rate_limit_per_minute?}` |
| `POST` | `/auth/token` | none (10/min per IP) | `{client_id, client_secret}` → `{access_token, token_type, expires_in}` |
| `POST` | `/feedback` | Bearer JWT | classify + auto-tag + store; body `{text, source?, metadata?}` |
| `POST` | `/feedback/classify` | Bearer JWT | same, without storing (preview) |
| `GET` | `/feedback` | Bearer JWT | list; `category`, `sentiment`, `from`, `to`, `limit` (≤100), `cursor` |
| `GET` | `/feedback/{id}` | Bearer JWT | one record (404 if it belongs to another client) |
| `GET` | `/dashboard/summary` | Bearer JWT | aggregation; `from`, `to` (default last 7 days), `bucket=hour\|day\|week`, `category` |

Every authenticated response carries `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; a 429 adds `Retry-After`. Every response carries an
`X-Request-Id` (echoed from the request if supplied).

`GET /feedback` is keyset-paginated: `next_cursor` encodes the last item's
`(created_at, id)`, so pages stay stable while new feedback arrives.

### Dashboard response

```json
{
  "window":  { "from": "2026-03-01T00:00:00.000Z", "to": "2026-03-10T00:00:00.000Z", "bucket": "day", "category": null },
  "totals":  { "total": 6, "positive": 2, "negative": 3, "neutral": 1, "avg_score": 0.0 },
  "by_category": [ { "category": "billing", "total": 2, "positive": 0, "negative": 2, "neutral": 0, "avg_score": -0.5 }, "…" ],
  "timeline":    [ { "bucket": "2026-03-02T00:00:00.000Z", "total": 2, "positive": 0, "negative": 2, "neutral": 0, "avg_score": -0.5 }, "…" ],
  "top_tags":    [ { "tag": "refund", "count": 2 }, "…" ]
}
```

## Data model and indexes

```sql
clients  (id, client_id UNIQUE, name, secret_hash, rate_limit_per_minute, created_at)
feedback (id, client_id → clients, text, source, category, tags JSON,
          sentiment_label CHECK IN (positive|negative|neutral), sentiment_score, sentiment_confidence,
          sentiment_model, metadata JSON NULL, created_at)

feedback_client_time           (client_id, created_at, id)                   -- plain list, dashboard window
feedback_client_category_time  (client_id, category, created_at, id)         -- ?category= on list and dashboard
feedback_client_sentiment_time (client_id, sentiment_label, created_at, id)  -- ?sentiment= on list
```

Timestamps are fixed-width ISO-8601 UTC text (`2026-11-26T09:15:30.123Z`), so
string order is time order: range predicates and `ORDER BY` work straight off
the index, and SQLite's date functions accept the format for bucketing.
Each index is *equality columns → (created_at, id)*, the exact sort and
cursor key of the list endpoint, so filter, order and keyset cursor come from
one index walk. The cursor is a row-value comparison,
`(created_at, id) < (?, ?)`, which SQLite turns into a single index seek.

`python -m app.explain --seed` prints the plans; `tests/test_query_plans.py`
asserts them on every run. No full scan, no temp b-tree for `ORDER BY`:

```
GET /feedback?category=billing            SEARCH feedback USING INDEX feedback_client_category_time (client_id=? AND category=?)
GET /feedback?sentiment=negative&from&to  SEARCH feedback USING INDEX feedback_client_sentiment_time (client_id=? AND sentiment_label=? AND created_at>? AND created_at<?)
GET /feedback?cursor=...                  SEARCH feedback USING INDEX feedback_client_time (client_id=? AND created_at<?)
GET /dashboard/summary  [totals]          SEARCH feedback USING INDEX feedback_client_time (client_id=? AND created_at>? AND created_at<?) | USE TEMP B-TREE FOR GROUP BY
GET /dashboard/summary?category=billing   SEARCH feedback USING INDEX feedback_client_category_time (client_id=? AND category=? AND created_at>? AND created_at<?) | USE TEMP B-TREE FOR GROUP BY
```

The dashboard is four `GROUP BY` queries over the same indexed window
predicate: per label, per category × label, per time bucket × label
(`strftime` for hour/day, `'-6 days', 'weekday 1'` for ISO weeks) and the top
ten tags via `json_each` over the JSON tags column. The temp b-tree in the
plans is the grouping itself, over the rows the index already narrowed down.

## Security

- **Client credentials.** `POST /auth/clients` (guarded by the admin key,
  compared in constant time) mints a `client_id` and a 256-bit `client_secret`.
  Only an scrypt hash of the secret is stored; it is returned once.
- **JWT.** `POST /auth/token` verifies the secret and signs an HS256 token
  (`sub` = client_id, `iss` = `feedback-engine`, `exp` = 1 h). Verification pins
  the algorithm (`alg: none` and key confusion are rejected), the issuer and the
  required claims. Every `/feedback` and `/dashboard` route requires it, and
  every query is scoped by the token's `sub`, so one client can never read
  another's records.
- **Per-client rate limit.** The client's `rate_limit_per_minute` is embedded in
  the token as `rpm`, so the limiter never touches the database. It is a token
  bucket per client: a burst up to the full limit, then a steady refill at
  `limit/minute`, with `Retry-After` computed from the deficit. Idle buckets are
  swept. The unauthenticated token endpoint has its own per-IP limiter.
- **Hygiene.** Pydantic validation of every body and query string, 5 kB text
  limit, flat metadata only, `CHECK` constraints and foreign keys in the schema,
  no secrets in the repo, and `APP_ENV=production` refuses the dev defaults.

The limiter state is per process. Several replicas would move the same
algorithm into Redis (one Lua script per `take`).

## Sentiment classification

`app/classifier.py` defines one small interface and two backends. Each returns
`label` (positive | negative | neutral), `score` (polarity, -1..1), `confidence`
(0..1) and `model`; the model name is stored with every record so a later model
swap can be told apart in the data.

| `SENTIMENT_BACKEND` | What it is | Cost |
| --- | --- | --- |
| `vader` (default) | VADER lexicon + rules; handles negation ("not good"), intensifiers, caps and punctuation | no download, ~0.1 ms per text in-process |
| `transformers` | Hugging Face `sentiment-analysis` pipeline (DistilBERT SST-2 by default); binary output mapped to neutral below a confidence threshold | ~1 GB: `pip install -r requirements-transformers.txt` |

Known limitation of the lexicon backend: text with no sentiment-bearing words
("I was charged twice and my refund request was ignored") scores exactly 0 and
is reported as neutral. The transformer backend is the answer when that
matters; the test suite exercises VADER and tests the transformer mapping
against a stub pipeline.

Auto-tagging (`app/tagger.py`) is deterministic whole-word keyword matching:
one `category` for grouping and zero or more triage `tags` (urgent, refund,
bug, feature-request, churn). It runs on the write path so a record is stored
with everything it needs in one insert.

## Measured locally

Sequential `curl` from the host on an Apple M1 laptop, one uvicorn worker,
VADER backend, about 200 rows in the table:

| Request | n | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| `POST /feedback` (JWT verify → classify → tag → insert) | 200 | 2.6 ms | 2.8 ms | 3.6 ms |
| `GET /dashboard/summary?bucket=hour` (four `GROUP BY` queries) | 100 | 2.4 ms | 2.7 ms | — |

The classifier itself averages 0.08 ms per text (`GET /metrics`); the rest is
HTTP, JWT verification and the SQLite write.

## Tests

```sh
.venv/bin/python -m pytest
```

43 tests against a temporary SQLite file per test, through the real HTTP layer
(`fastapi.testclient`) with an injected clock for the rate limiter:

- **auth**: admin key, hashed secrets, token claims, one message for every bad
  credential, per-IP limit on the token endpoint, forged / expired /
  wrong-issuer / `alg: none` tokens, production refusing default secrets
- **rate limit**: bucket maths, key isolation, idle sweep, limit change, the
  per-client limit from the JWT end to end with `Retry-After`
- **feedback**: classify + tag + store, validation, preview without storing,
  tenant-scoped reads, filters, keyset pagination across identical timestamps
- **dashboard**: totals, per category, hour / day / ISO-week buckets, top tags,
  category filter, window validation, tenant isolation
- **query plans**: `EXPLAIN QUERY PLAN` for every list and dashboard query must
  seek on a compound index with no scan and no sort step
- **tagger** and **classifier** units

GitHub Actions runs the suite and `python -m app.explain --seed` on every push.

## Configuration

Everything is read from the environment; every variable has a development
default.

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_ENV` | `dev` | `production` refuses the default secrets |
| `DATABASE_PATH` | `feedback.db` | SQLite file, created on first start |
| `JWT_SECRET` | dev value | HS256 signing key |
| `JWT_TTL_SECONDS` | `3600` | token lifetime |
| `ADMIN_API_KEY` | dev value | guards `POST /auth/clients` |
| `DEFAULT_RATE_LIMIT_PER_MINUTE` | `60` | used when a client is created without an explicit limit |
| `TOKEN_ENDPOINT_LIMIT_PER_MINUTE` | `10` | per-IP limit on `POST /auth/token` |
| `SENTIMENT_BACKEND` | `vader` | `vader` or `transformers` |

## Layout

```
app/
  main.py          create_app(settings, classifier, clock): lifespan, routers, request ids
  settings.py      environment → Settings, production check
  db.py            schema + indexes, connections, ISO timestamp helpers
  security.py      scrypt secret hashing, HS256 JWT sign/verify, constant-time compare
  ratelimit.py     TokenBucket, RateLimiter
  deps.py          get_db, require_admin, current_client, rate_limited, token_endpoint_limited
  classifier.py    Sentiment, VaderClassifier, TransformersClassifier
  tagger.py        category + tags
  repos.py         ClientRepo, FeedbackRepo (SQL builders + execution, keyset cursor, summary)
  schemas.py       pydantic request/response models
  errors.py        ApiError and the single error shape
  routers/         health.py, auth.py, feedback.py, dashboard.py
  explain.py       python -m app.explain [--seed]: query plans
tests/             pytest, see above
.github/workflows/ci.yml
```

## Design notes

- **Why one service.** The classifier is a Python library call that takes a
  fraction of a millisecond; putting it behind a second HTTP service would add
  a network hop, a second deployment and a shared secret for no gain at this
  size. The classifier still sits behind an interface, so moving it out later
  is a change to one module.
- **Why SQLite and the standard-library driver.** The workload is small
  writes and indexed reads from one process. SQLite in WAL mode handles that
  with nothing to run or configure, and writing the SQL by hand keeps the
  interesting parts (compound indexes, row-value cursors, `GROUP BY` with date
  bucketing, `json_each`) visible instead of hidden behind an ORM.
- **Why scrypt from `hashlib`.** Client secrets are 256-bit random strings, so
  the hash is defence in depth rather than the only barrier; a memory-hard KDF
  that ships with Python beats adding a dependency for it.
- **Why the rate limit lives in the JWT.** The hot path needs the client's
  limit on every request; reading it from the signed token costs nothing and
  cannot be tampered with. The trade-off is that a changed limit applies at the
  next token (≤1 h).
- **Why synchronous classification.** The response includes the stored record
  with its sentiment, which is what "real time" means for the caller; at a few
  milliseconds end to end a queue would add more than it saves.
- **Why keyset pagination.** Offsets degrade linearly and skip or repeat rows
  under concurrent inserts; `(created_at, id)` cursors are one index seek and
  stable, and the `id` tiebreaker is in every index so the sort is free.

## License

MIT
