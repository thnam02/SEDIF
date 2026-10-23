"""Hàng đợi mule B01: nhận từ ít nhất 3 người lạ rồi chuyển đi ít nhất 50% trong 24 giờ."""

from __future__ import annotations

import json
import os
import urllib.request

from engine.events import connect

DAY_US = 86_400 * 1_000_000
SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
    review_id text PRIMARY KEY,
    ts timestamptz NOT NULL,
    account_hash text NOT NULL,
    inbound_amount bigint NOT NULL,
    outbound_amount bigint NOT NULL,
    strangers integer NOT NULL,
    status text NOT NULL
);
"""


def ensure_review(conn) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def _b01(store, account: str) -> bool:
    meta = store.redis.hgetall(f"f:meta:{account}")
    return bool(meta) and str(meta.get("b01", "0")) == "1"


def consider(store, event: dict, when) -> None:
    if event.get("type") != "txn":
        return
    targets = []
    account = event.get("account")
    payee = event.get("payee")
    if event.get("xfer") and account and _b01(store, account):
        targets.append(account)
    if payee and payee != "-" and _b01(store, payee):
        targets.append(payee)
    if not targets:
        return
    store.flush()
    for account in dict.fromkeys(targets):
        _open(store, account, int(event["ts"]), when)


def _open(store, account: str, as_of: int, when) -> None:
    start = as_of - DAY_US
    inbound = store.redis.zrangebyscore(f"f:in:{account}", f"({start}", as_of)
    older = store.redis.zrangebyscore(f"f:in:{account}", "-inf", start)
    known = set()
    for member in older:
        parts = member.split("|")
        if len(parts) >= 3:
            known.add(parts[2])
    strangers: dict[str, int] = {}
    first_ts = None
    for member in inbound:
        ts, _seq, sender, amount = member.split("|", 3)
        if sender in known:
            continue
        strangers[sender] = strangers.get(sender, 0) + int(amount)
        stamp = int(ts)
        first_ts = stamp if first_ts is None else min(first_ts, stamp)
    if len(strangers) < 3 or first_ts is None:
        return
    inbound_amount = sum(strangers.values())
    outbound = store.redis.zrangebyscore(f"f:xf:{account}", f"({first_ts}", min(as_of, first_ts + DAY_US))
    outbound_amount = 0
    for member in outbound:
        outbound_amount += int(member.split("|")[2])
    if inbound_amount <= 0 or outbound_amount * 2 < inbound_amount:
        return
    review_id = f"rev-{account[:16]}-{as_of // DAY_US}"
    with connect() as conn:
        ensure_review(conn)
        conn.execute(
            """
            INSERT INTO review_queue (review_id, ts, account_hash, inbound_amount, outbound_amount, strangers, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'open')
            ON CONFLICT (review_id) DO NOTHING
            """,
            (review_id, when, account, inbound_amount, outbound_amount, len(strangers)),
        )
        conn.commit()


def queue(conn) -> list[dict]:
    ensure_review(conn)
    rows = conn.execute(
        """
        SELECT review_id, ts, account_hash, inbound_amount, outbound_amount, strangers, status
        FROM review_queue WHERE status = 'open' ORDER BY ts, review_id
        """
    ).fetchall()
    return [
        {
            "review_id": row[0],
            "ts": row[1],
            "account_hash": row[2],
            "inbound_amount": int(row[3]),
            "outbound_amount": int(row[4]),
            "strangers": int(row[5]),
            "status": row[6],
        }
        for row in rows
    ]


def confirm(conn, review_id: str) -> dict | None:
    ensure_review(conn)
    row = conn.execute(
        "SELECT account_hash, status, ts FROM review_queue WHERE review_id = %s",
        (review_id,),
    ).fetchone()
    if row is None:
        return None
    account_hash, status, listed_at = row
    if status != "confirmed":
        conn.execute("UPDATE review_queue SET status = 'confirmed' WHERE review_id = %s", (review_id,))
        conn.commit()
        _report(account_hash, listed_at.isoformat())
    return {"review_id": review_id, "account_hash": account_hash, "status": "confirmed"}


def _report(account_hash: str, listed_at: str) -> None:
    url = os.environ.get("SIMO_URL", "http://127.0.0.1:8093").rstrip("/") + "/simo/report"
    body = json.dumps({"account_hash": account_hash, "source": "bank", "listed_at": listed_at}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
