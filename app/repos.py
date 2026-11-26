"""SQL for the two tables. Query builders are separate from execution so the
same SQL can be EXPLAINed (app/explain.py, tests/test_query_plans.py)."""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from secrets import token_hex
from typing import Any

from .classifier import Sentiment
from .db import utc_now_iso
from .security import hash_secret, verify_secret

LABELS = ("positive", "negative", "neutral")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class ClientRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, name: str, rate_limit_per_minute: int) -> dict[str, Any]:
        client_id = f"ck_{token_hex(12)}"
        client_secret = f"cs_{token_hex(32)}"      # 256 bits, shown once; only its hash is stored
        created_at = utc_now_iso()
        self.conn.execute(
            "INSERT INTO clients (client_id, name, secret_hash, rate_limit_per_minute, created_at) VALUES (?, ?, ?, ?, ?)",
            (client_id, name, hash_secret(client_secret), rate_limit_per_minute, created_at),
        )
        return {"client_id": client_id, "client_secret": client_secret, "name": name,
                "rate_limit_per_minute": rate_limit_per_minute, "created_at": created_at}

    def get(self, client_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()

    @staticmethod
    def verify(row: sqlite3.Row, client_secret: str) -> bool:
        return verify_secret(client_secret, row["secret_hash"])


# ---- keyset cursor: the last row's (created_at, id), opaque to the caller ----

def encode_cursor(created_at: str, id_: int) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{id_}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created_at, id_ = raw.split("|")
        if not _ISO_RE.match(created_at):
            return None
        return created_at, int(id_)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


# SQLite date modifiers do the bucketing; the output is again ISO UTC text.
BUCKET_EXPR = {
    "hour": "strftime('%Y-%m-%dT%H:00:00.000Z', created_at)",
    "day": "strftime('%Y-%m-%dT00:00:00.000Z', created_at)",
    # back 6 days then forward to the next Monday = the Monday on or before created_at
    "week": "strftime('%Y-%m-%dT00:00:00.000Z', created_at, '-6 days', 'weekday 1')",
}


def _row_to_feedback(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "text": row["text"],
        "source": row["source"],
        "category": row["category"],
        "tags": json.loads(row["tags"]),
        "sentiment": {
            "label": row["sentiment_label"],
            "score": row["sentiment_score"],
            "confidence": row["sentiment_confidence"],
            "model": row["sentiment_model"],
        },
        "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
        "created_at": row["created_at"],
    }


def _fold(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """[{label, n, avg_score}, ...] for one group -> one flat row."""
    out: dict[str, Any] = {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "avg_score": 0.0}
    weighted = 0.0
    for r in rows:
        if r["label"] not in LABELS:
            continue
        out[r["label"]] += r["n"]
        out["total"] += r["n"]
        weighted += (r["avg_score"] or 0.0) * r["n"]
    out["avg_score"] = round(weighted / out["total"], 4) if out["total"] else 0.0
    return out


class FeedbackRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, *, client_id: str, text: str, source: str, category: str, tags: list[str],
               sentiment: Sentiment, metadata: dict[str, Any] | None = None,
               created_at: str | None = None) -> dict[str, Any]:
        created_at = created_at or utc_now_iso()
        cur = self.conn.execute(
            """INSERT INTO feedback (client_id, text, source, category, tags, sentiment_label, sentiment_score,
                                     sentiment_confidence, sentiment_model, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_id, text, source, category, json.dumps(tags), sentiment.label, sentiment.score,
             sentiment.confidence, sentiment.model, json.dumps(metadata) if metadata else None, created_at),
        )
        return self.get(client_id, cur.lastrowid)  # type: ignore[return-value]

    def get(self, client_id: str, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM feedback WHERE id = ? AND client_id = ?", (id_, client_id)).fetchone()
        return _row_to_feedback(row) if row else None

    # -- list ---------------------------------------------------------------

    @staticmethod
    def list_query(client_id: str, *, category: str | None = None, sentiment: str | None = None,
                   from_: str | None = None, to: str | None = None,
                   cursor: tuple[str, int] | None = None, limit: int = 20) -> tuple[str, list[Any]]:
        where = ["client_id = ?"]
        params: list[Any] = [client_id]
        if category:
            where.append("category = ?"); params.append(category)
        if sentiment:
            where.append("sentiment_label = ?"); params.append(sentiment)
        if from_:
            where.append("created_at >= ?"); params.append(from_)
        if to:
            where.append("created_at < ?"); params.append(to)
        if cursor:
            # Row-value comparison: one range predicate the index can seek on.
            where.append("(created_at, id) < (?, ?)"); params.extend(cursor)
        params.append(limit + 1)  # one extra row tells us whether there is a next page
        sql = f"SELECT * FROM feedback WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT ?"
        return sql, params

    def list(self, client_id: str, *, limit: int = 20, **filters: Any) -> tuple[list[dict[str, Any]], str | None]:
        sql, params = self.list_query(client_id, limit=limit, **filters)
        rows = self.conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more else None
        return [_row_to_feedback(r) for r in page], next_cursor

    # -- dashboard ------------------------------------------------------------

    @staticmethod
    def summary_queries(client_id: str, *, from_: str, to: str, bucket: str,
                        category: str | None = None) -> dict[str, tuple[str, list[Any]]]:
        def where(alias: str = "") -> tuple[str, list[Any]]:
            p = f"{alias}." if alias else ""
            clauses = [f"{p}client_id = ?", f"{p}created_at >= ?", f"{p}created_at < ?"]
            params: list[Any] = [client_id, from_, to]
            if category:
                clauses.append(f"{p}category = ?"); params.append(category)
            return " AND ".join(clauses), params

        w, p = where()
        wf, pf = where("f")
        agg = "COUNT(*) AS n, AVG(sentiment_score) AS avg_score"
        return {
            "totals": (f"SELECT sentiment_label AS label, {agg} FROM feedback WHERE {w} GROUP BY sentiment_label", p),
            "by_category": (f"SELECT category, sentiment_label AS label, {agg} FROM feedback WHERE {w} "
                            "GROUP BY category, sentiment_label", p),
            "timeline": (f"SELECT {BUCKET_EXPR[bucket]} AS bucket, sentiment_label AS label, {agg} FROM feedback "
                         f"WHERE {w} GROUP BY bucket, sentiment_label ORDER BY bucket", p),
            "top_tags": (f"SELECT j.value AS tag, COUNT(*) AS n FROM feedback AS f, json_each(f.tags) AS j "
                         f"WHERE {wf} GROUP BY tag ORDER BY n DESC, tag LIMIT 10", pf),
        }

    def summary(self, client_id: str, *, from_: str, to: str, bucket: str, category: str | None = None) -> dict[str, Any]:
        q = self.summary_queries(client_id, from_=from_, to=to, bucket=bucket, category=category)
        run = lambda name: self.conn.execute(*q[name]).fetchall()  # noqa: E731

        def grouped(rows: list[sqlite3.Row], key: str) -> dict[str, list[sqlite3.Row]]:
            out: dict[str, list[sqlite3.Row]] = {}
            for r in rows:
                out.setdefault(r[key], []).append(r)
            return out

        by_category = [{"category": cat, **_fold(rows)} for cat, rows in grouped(run("by_category"), "category").items()]
        by_category.sort(key=lambda r: (-r["total"], r["category"]))
        timeline = [{"bucket": b, **_fold(rows)} for b, rows in grouped(run("timeline"), "bucket").items()]
        timeline.sort(key=lambda r: r["bucket"])
        return {
            "window": {"from": from_, "to": to, "bucket": bucket, "category": category},
            "totals": _fold(run("totals")),
            "by_category": by_category,
            "timeline": timeline,
            "top_tags": [{"tag": r["tag"], "count": r["n"]} for r in run("top_tags")],
        }
