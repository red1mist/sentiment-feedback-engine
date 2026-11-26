"""SQLite: schema, connections and the timestamp format used throughout.

One file, WAL mode. A SQLite connection is cheap to open, so every request gets
its own (deps.get_db) and nothing is shared between threads. The schema is
applied once at startup and is idempotent.

Timestamps are ISO-8601 UTC text with millisecond precision and a trailing Z
(2026-11-26T09:15:30.123Z). The fixed width makes string comparison equal to
time comparison, so range predicates and ORDER BY work straight off the index,
and SQLite's date functions accept the format for bucketing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id                    INTEGER PRIMARY KEY,
    client_id             TEXT    NOT NULL UNIQUE,
    name                  TEXT    NOT NULL,
    secret_hash           TEXT    NOT NULL,
    rate_limit_per_minute INTEGER NOT NULL,
    created_at            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id                   INTEGER PRIMARY KEY,
    client_id            TEXT NOT NULL REFERENCES clients(client_id),
    text                 TEXT NOT NULL,
    source               TEXT NOT NULL,
    category             TEXT NOT NULL,
    tags                 TEXT NOT NULL,            -- JSON array of strings
    sentiment_label      TEXT NOT NULL CHECK (sentiment_label IN ('positive', 'negative', 'neutral')),
    sentiment_score      REAL NOT NULL,            -- polarity, -1 .. 1
    sentiment_confidence REAL NOT NULL,            -- 0 .. 1
    sentiment_model      TEXT NOT NULL,
    metadata             TEXT,                     -- JSON object or NULL
    created_at           TEXT NOT NULL
);

-- Equality columns first, then the (created_at, id) pair the list endpoint
-- sorts and paginates on, so filter + sort + cursor come from one index and
-- SQLite never needs a temp b-tree. Each index matches a query the API issues.
CREATE INDEX IF NOT EXISTS feedback_client_time
    ON feedback (client_id, created_at, id);                   -- plain list, dashboard window
CREATE INDEX IF NOT EXISTS feedback_client_category_time
    ON feedback (client_id, category, created_at, id);         -- ?category= on list and dashboard
CREATE INDEX IF NOT EXISTS feedback_client_sentiment_time
    ON feedback (client_id, sentiment_label, created_at, id);  -- ?sentiment= on list
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")  # persistent; readers never block the writer
    conn.executescript(SCHEMA)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utc_now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
