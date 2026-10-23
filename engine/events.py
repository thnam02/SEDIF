"""Sổ events trên Postgres. Chỉ thêm dòng. Trùng event_id thì giữ bản đã lưu."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id text PRIMARY KEY,
    type text NOT NULL,
    ts timestamptz NOT NULL,
    account_hash text NOT NULL,
    counterparty_hash text,
    body jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS events_account_hash_ts ON events (account_hash, ts);
CREATE INDEX IF NOT EXISTS events_counterparty_hash_ts ON events (counterparty_hash, ts);
"""

GUARD = """
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'events chỉ được thêm';
END;
$$;
DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_append_only();
DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate
    BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();
"""

INSERT = """
INSERT INTO events (event_id, type, ts, account_hash, counterparty_hash, body)
VALUES (%(event_id)s, %(type)s, %(ts)s, %(account_hash)s, %(counterparty_hash)s, %(body)s::jsonb)
ON CONFLICT (event_id) DO NOTHING
"""


def database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL thiếu, không khởi động")
    return raw


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute(GUARD)
    conn.commit()


def insert_event(conn: psycopg.Connection, event: dict[str, Any]) -> bool:
    row = {
        "event_id": event["event_id"],
        "type": event["type"],
        "ts": event["ts"],
        "account_hash": event["account_hash"],
        "counterparty_hash": event.get("counterparty_hash"),
        "body": json.dumps(event, ensure_ascii=False),
    }
    cur = conn.execute(INSERT + " RETURNING event_id", row)
    found = cur.fetchone()
    conn.commit()
    return found is not None


def insert_many(conn: psycopg.Connection, events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    rows = [
        {
            "event_id": event["event_id"],
            "type": event["type"],
            "ts": event["ts"],
            "account_hash": event["account_hash"],
            "counterparty_hash": event.get("counterparty_hash"),
            "body": json.dumps(event, ensure_ascii=False),
        }
        for event in events
    ]
    with conn.cursor() as cur:
        cur.executemany(INSERT, rows)
        stored = cur.rowcount
    conn.commit()
    return int(stored)


def counts_by_type(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT type, count(*) FROM events GROUP BY type").fetchall()
    return {str(kind): int(count) for kind, count in rows}
