"""Sổ decisions và responses. Chỉ thêm dòng."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from engine.events import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id text PRIMARY KEY,
    ts timestamptz NOT NULL,
    model_version text NOT NULL,
    profile text NOT NULL,
    sender_hash text NOT NULL,
    payee_hash text,
    features jsonb NOT NULL,
    p double precision NOT NULL,
    level text NOT NULL,
    actions jsonb NOT NULL,
    families_hit jsonb NOT NULL,
    reasons jsonb NOT NULL,
    funds_status text NOT NULL,
    hold_until timestamptz,
    countdown_until timestamptz,
    segment text,
    has_guardian boolean NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_level ON decisions (level);
CREATE TABLE IF NOT EXISTS responses (
    id bigserial PRIMARY KEY,
    decision_id text NOT NULL,
    ts timestamptz NOT NULL,
    level text NOT NULL,
    funds_status text NOT NULL,
    gate text NOT NULL,
    hold_until timestamptz,
    countdown_until timestamptz,
    body jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS responses_decision ON responses (decision_id, id);
"""

GUARD = """
CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit chỉ được thêm';
END;
$$;
DROP TRIGGER IF EXISTS decisions_no_update ON decisions;
CREATE TRIGGER decisions_no_update
    BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION audit_append_only();
DROP TRIGGER IF EXISTS decisions_no_truncate ON decisions;
CREATE TRIGGER decisions_no_truncate
    BEFORE TRUNCATE ON decisions
    FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();
DROP TRIGGER IF EXISTS responses_no_update ON responses;
CREATE TRIGGER responses_no_update
    BEFORE UPDATE OR DELETE ON responses
    FOR EACH ROW EXECUTE FUNCTION audit_append_only();
DROP TRIGGER IF EXISTS responses_no_truncate ON responses;
CREATE TRIGGER responses_no_truncate
    BEFORE TRUNCATE ON responses
    FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();
"""


def audit_conn():
    conn = connect()
    conn.row_factory = dict_row
    return conn


def ensure_audit(conn) -> None:
    conn.execute(SCHEMA)
    conn.execute(GUARD)
    conn.commit()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def insert_decision(conn, row: dict) -> bool:
    found = conn.execute(
        """
        INSERT INTO decisions (
            decision_id, ts, model_version, profile, sender_hash, payee_hash, features, p,
            level, actions, families_hit, reasons, funds_status, hold_until, countdown_until,
            segment, has_guardian, payload
        ) VALUES (
            %(decision_id)s, %(ts)s, %(model_version)s, %(profile)s, %(sender_hash)s, %(payee_hash)s,
            %(features)s::jsonb, %(p)s, %(level)s, %(actions)s::jsonb, %(families_hit)s::jsonb,
            %(reasons)s::jsonb, %(funds_status)s, %(hold_until)s, %(countdown_until)s,
            %(segment)s, %(has_guardian)s, %(payload)s::jsonb
        )
        ON CONFLICT (decision_id) DO NOTHING
        RETURNING decision_id
        """,
        {
            "decision_id": row["decision_id"],
            "ts": row["ts"],
            "model_version": row["model_version"],
            "profile": row["profile"],
            "sender_hash": row["sender_hash"],
            "payee_hash": row.get("payee_hash"),
            "features": _dump(row["features"]),
            "p": row["p"],
            "level": row["level"],
            "actions": _dump(row["actions"]),
            "families_hit": _dump(row["families_hit"]),
            "reasons": _dump(row["reasons"]),
            "funds_status": row["funds_status"],
            "hold_until": row["hold_until"],
            "countdown_until": row["countdown_until"],
            "segment": row["segment"],
            "has_guardian": row["has_guardian"],
            "payload": _dump(row["payload"]),
        },
    ).fetchone()
    conn.commit()
    return found is not None


def insert_response(conn, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO responses (decision_id, ts, level, funds_status, gate, hold_until, countdown_until, body)
        VALUES (%(decision_id)s, %(ts)s, %(level)s, %(funds_status)s, %(gate)s, %(hold_until)s, %(countdown_until)s, %(body)s::jsonb)
        """,
        {
            "decision_id": row["decision_id"],
            "ts": row["ts"],
            "level": row["level"],
            "funds_status": row["funds_status"],
            "gate": row["gate"],
            "hold_until": row["hold_until"],
            "countdown_until": row["countdown_until"],
            "body": _dump(row["body"]),
        },
    )
    conn.commit()


def _state(record, latest) -> dict:
    source = latest or record
    return {
        "decision_id": record["decision_id"],
        "level": source["level"],
        "funds_status": source["funds_status"],
        "hold_until": source["hold_until"],
        "countdown_until": source["countdown_until"],
        "gate": source["gate"] if latest else "none",
        "decided_at": record["ts"],
        "has_guardian": bool(record["has_guardian"]),
        "segment": record["segment"],
        "sender_hash": record["sender_hash"],
    }


def load_state(conn, decision_id: str) -> dict | None:
    conn.row_factory = dict_row
    record = conn.execute("SELECT * FROM decisions WHERE decision_id = %s", (decision_id,)).fetchone()
    if record is None:
        return None
    latest = conn.execute(
        """
        SELECT level, funds_status, gate, hold_until, countdown_until
        FROM responses WHERE decision_id = %s ORDER BY id DESC LIMIT 1
        """,
        (decision_id,),
    ).fetchone()
    return _state(dict(record), dict(latest) if latest is not None else None)


def list_decisions(
    conn,
    level: str | None,
    limit: int = 50,
    cursor_ts=None,
    cursor_id: str | None = None,
    from_ts=None,
    to_ts=None,
) -> list[dict]:
    conn.row_factory = dict_row
    rows = conn.execute(
        """
        SELECT d.decision_id,
               d.ts,
               COALESCE(r.level, d.level) AS level,
               COALESCE(r.funds_status, d.funds_status) AS funds_status,
               CASE WHEN r.id IS NULL THEN d.hold_until ELSE r.hold_until END AS hold_until,
               CASE WHEN r.id IS NULL THEN d.countdown_until ELSE r.countdown_until END AS countdown_until
        FROM decisions d
        LEFT JOIN LATERAL (
            SELECT id, level, funds_status, hold_until, countdown_until
            FROM responses WHERE decision_id = d.decision_id ORDER BY id DESC LIMIT 1
        ) r ON true
        WHERE (%(level)s::text IS NULL OR COALESCE(r.level, d.level) = %(level)s::text)
          AND (%(from_ts)s::timestamptz IS NULL OR d.ts >= %(from_ts)s::timestamptz)
          AND (%(to_ts)s::timestamptz IS NULL OR d.ts < %(to_ts)s::timestamptz)
          AND (
                %(cursor_ts)s::timestamptz IS NULL
                OR (d.ts, d.decision_id) > (%(cursor_ts)s::timestamptz, %(cursor_id)s::text)
              )
        ORDER BY d.ts, d.decision_id
        LIMIT %(limit)s
        """,
        {
            "level": level,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "limit": int(limit),
        },
    ).fetchall()
    return [dict(row) for row in rows]


def decision_payload(conn, decision_id: str) -> dict | None:
    conn.row_factory = dict_row
    row = conn.execute("SELECT payload FROM decisions WHERE decision_id = %s", (decision_id,)).fetchone()
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        return json.loads(payload)
    return dict(payload)


def reset_countdown(conn, sender_hash: str, until: datetime, now: datetime) -> None:
    conn.row_factory = dict_row
    rows = conn.execute(
        """
        SELECT d.decision_id
        FROM decisions d
        LEFT JOIN LATERAL (
            SELECT countdown_until, funds_status FROM responses
            WHERE decision_id = d.decision_id ORDER BY id DESC LIMIT 1
        ) r ON true
        WHERE d.sender_hash = %s
          AND COALESCE(r.countdown_until, d.countdown_until) IS NOT NULL
          AND COALESCE(r.funds_status, d.funds_status) <> 'cancelled'
        """,
        (sender_hash,),
    ).fetchall()
    for row in rows:
        decision_id = row["decision_id"]
        state = load_state(conn, decision_id)
        if state is None or state["countdown_until"] is None:
            continue
        insert_response(
            conn,
            {
                "decision_id": decision_id,
                "ts": now,
                "level": state["level"],
                "funds_status": state["funds_status"],
                "gate": state["gate"],
                "hold_until": state["hold_until"],
                "countdown_until": until,
                "body": {"kind": "new_call"},
            },
        )
