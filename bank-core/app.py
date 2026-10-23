"""Bank-core. Đóng dấu đồng hồ demo, gọi engine bằng API key, giữ tiền medium/high."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd
import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from clock import DemoClock
from engine.common.hashing import account_hash, require_key
from load import CUT, OBSERVED, PERSONAS, load

CLOCK = DemoClock(speed=float(os.environ.get("DEMO_CLOCK_SPEED", "60")))
SKIP: set[str] = set()
SESSIONS: set[str] = set()
FEED: list[tuple[datetime, dict]] = []
FEED_CURSOR = 0


def database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL thiếu, không khởi động")
    return raw


def connect():
    return psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)


def engine_post(path: str, body: dict) -> dict:
    url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8091").rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(body, default=str).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": os.environ.get("FIDES_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise HTTPException(exc.code, detail) from exc


def _us(stamp: datetime) -> int:
    return int(stamp.timestamp() * 1_000_000)


def touch_session(account: str, device: dict | None, now: datetime) -> str:
    session = f"demo-{account}"
    hashed = account_hash(account)
    if account not in SESSIONS:
        mode = (device or {}).get("acct_input") or "typed"
        with connect() as conn:
            customer = conn.execute("SELECT customer_id FROM bank.accounts WHERE id = %s", (account,)).fetchone()
        engine_post(
            "/events",
            {
                "type": "session",
                "session": session,
                "account": hashed,
                "start": _us(now),
                "end": _us(datetime.fromisoformat("2026-09-29T00:00:00+07:00")),
                "customer": (customer or {}).get("customer_id") or "",
                "first_input": mode,
            },
        )
        SESSIONS.add(account)
    if device:
        engine_post(
            "/events",
            {
                "type": "device_signal",
                "ts": _us(now),
                "session": session,
                "in_call": "1" if device.get("in_call") else "0",
                "ended": "1" if device.get("call_ended_in_session") else "0",
            },
        )
        if device.get("acct_input"):
            engine_post(
                "/events",
                {"type": "session_event", "ts": _us(now), "session": session, "event_type": f"acct_input_{device['acct_input']}"},
            )
    return session


def post_ledger(event: dict) -> None:
    engine_post("/events", event)


def status_of(level: str, funds: str, hung_up: bool) -> str:
    if funds == "cancelled":
        return "cancelled"
    if funds == "released":
        return "done" if level in {"none", "banner"} else "released"
    if level == "high" and not hung_up:
        return "blocked_pending_hangup"
    if level == "medium":
        return "needs_verification"
    return "held"


def credit(conn, account: str, amount: int) -> None:
    conn.execute("UPDATE bank.accounts SET balance = balance + %s WHERE id = %s", (amount, account))


def debit(conn, account: str, amount: int) -> None:
    row = conn.execute("SELECT balance FROM bank.accounts WHERE id = %s", (account,)).fetchone()
    if row is None:
        raise HTTPException(404, "không có tài khoản")
    if int(row["balance"]) < amount:
        raise HTTPException(400, "không đủ số dư")
    conn.execute("UPDATE bank.accounts SET balance = balance - %s WHERE id = %s", (amount, account))


def entry(conn, account: str, tx_id: str, ts, direction: str, amount: int, tx_type: str, purpose: str, status: str) -> None:
    conn.execute(
        """
        INSERT INTO bank.entries (account_id, tx_id, ts, direction, amount, tx_type, purpose_text, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (account, tx_id, ts, direction, amount, tx_type, purpose, status),
    )


def release_payee(conn, transfer: dict, now: datetime) -> None:
    if transfer["posted"]:
        return
    owned = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (transfer["to_account"],)).fetchone()
    if owned:
        credit(conn, transfer["to_account"], int(transfer["amount"]))
        entry(conn, transfer["to_account"], transfer["id"], now, "in", int(transfer["amount"]), "transfer", transfer["purpose_text"], "done")
    balance = conn.execute("SELECT balance FROM bank.accounts WHERE id = %s", (transfer["from_account"],)).fetchone()
    session = f"demo-{transfer['from_account']}"
    post_ledger(
        {
            "event_id": f"txn:{transfer['id']}",
            "type": "txn",
            "ts": now.isoformat(),
            "account_hash": account_hash(transfer["from_account"]),
            "counterparty_hash": account_hash(transfer["to_account"]),
            "tx_type": "transfer",
            "amount": int(transfer["amount"]),
            "balance": int(balance["balance"]) if balance else 0,
            "session_id": session,
        }
    )
    conn.execute("UPDATE bank.transfers SET posted = true WHERE id = %s", (transfer["id"],))


def apply_funds(conn, transfer: dict, previous: str, funds: str, now: datetime) -> None:
    if funds == "released" and previous != "released":
        release_payee(conn, transfer, now)
    if funds == "cancelled" and previous != "cancelled":
        credit(conn, transfer["from_account"], int(transfer["amount"]))
        entry(conn, transfer["from_account"], transfer["id"] + ":refund", now, "in", int(transfer["amount"]), "transfer", "hoàn", "cancelled")


class TransferIn(BaseModel):
    from_account: str
    to_account: str
    amount: int
    purpose_text: str | None = None
    device: dict | None = None


class RespondIn(BaseModel):
    who_guides: str | None = None
    guardian_confirm: bool | None = None
    hung_up: bool | None = None
    proceed: bool | None = None
    cancel: bool | None = None


class ProductIn(BaseModel):
    account_id: str
    amount: int
    device: dict | None = None


class LimitIn(BaseModel):
    account_id: str
    limit_per_txn: int
    limit_per_day: int
    device: dict | None = None


class GuardianIn(BaseModel):
    account_id: str
    guardian_phone_hash: str
    relation: str


class PersonaIn(BaseModel):
    customer_id: str


app = FastAPI(title="Bank core")


def _persona_accounts(customer_id: str) -> set[str]:
    payload = json.loads(PERSONAS.read_text(encoding="utf-8"))
    for person in payload["personas"]:
        if person["customer_id"] == customer_id:
            return set(person["account_ids"])
    return set()


def _build_feed() -> None:
    global FEED
    start = pd.Timestamp(CUT)
    end = pd.Timestamp("2026-09-29T00:00:00+07:00")
    tx = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type", "from_balance_before"])
    tx["ts"] = pd.to_datetime(tx["ts"])
    tx = tx.loc[(tx["ts"] >= start) & (tx["ts"] < end)]
    rows = []
    for record in tx.itertuples(index=False):
        stamp = pd.Timestamp(record.ts).to_pydatetime()
        sender = str(record.from_account)
        rows.append(
            (
                stamp,
                sender,
                {
                    "event_id": f"txn:{record.tx_id}",
                    "type": "txn",
                    "ts": stamp.isoformat(),
                    "account_hash": account_hash(sender),
                    "counterparty_hash": account_hash(str(record.to_account)) if pd.notna(record.to_account) else None,
                    "tx_type": str(record.tx_type),
                    "amount": int(record.amount),
                    "balance": int(record.from_balance_before or 0),
                },
            )
        )
    rows.sort(key=lambda item: item[0])
    FEED = rows


def _play() -> None:
    global FEED_CURSOR
    while True:
        if CLOCK.paused or FEED_CURSOR >= len(FEED):
            threading.Event().wait(0.2)
            continue
        stamp, sender, event = FEED[FEED_CURSOR]
        if CLOCK.now() < stamp:
            threading.Event().wait(0.05)
            continue
        FEED_CURSOR += 1
        if sender in SKIP:
            continue
        try:
            post_ledger(event)
        except Exception:
            continue


@app.on_event("startup")
def startup() -> None:
    require_key()
    with connect() as conn:
        load(conn)
    if os.environ.get("DEMO_REPLAY", "1") == "1":
        threading.Thread(target=_build_feed, daemon=True).start()
        threading.Thread(target=_play, daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "bank-core"}


@app.get("/clock")
def clock() -> dict:
    return CLOCK.state()


@app.post("/clock/pause")
def pause() -> dict:
    CLOCK.pause()
    return CLOCK.state()


@app.post("/clock/resume")
def resume() -> dict:
    CLOCK.resume()
    return CLOCK.state()


@app.post("/demo/persona")
def persona(body: PersonaIn) -> dict:
    global SKIP
    SKIP = _persona_accounts(body.customer_id)
    return {"customer_id": body.customer_id, "skipped_accounts": sorted(SKIP)}


@app.get("/accounts/{account_id}")
def get_account(account_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "không có tài khoản")
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "holder_name": row["holder_name"],
        "segment": row["segment"] if row["segment"] in {"thường", "cao tuổi", "doanh nghiệp nhỏ"} else "thường",
        "balance": int(row["balance"]),
        "status": row["status"] if row["status"] in {"open", "frozen", "closed"} else "open",
        "has_guardian": bool(row["has_guardian"]),
    }


@app.get("/accounts/{account_id}/history")
def history(account_id: str, before: str | None = None) -> dict:
    cutoff = datetime.fromisoformat(before) if before else CLOCK.now()
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        rows = conn.execute(
            """
            SELECT tx_id, ts, direction, amount, tx_type, purpose_text, status
            FROM bank.entries WHERE account_id = %s AND ts < %s ORDER BY ts, id
            """,
            (account_id, cutoff),
        ).fetchall()
    return {
        "account_id": account_id,
        "entries": [
            {
                "tx_id": row["tx_id"],
                "ts": row["ts"].isoformat(),
                "direction": row["direction"],
                "amount": int(row["amount"]),
                "tx_type": row["tx_type"],
                "purpose_text": row["purpose_text"],
                "status": row["status"],
            }
            for row in rows
        ],
    }


@app.get("/beneficiaries/{beneficiary_id}")
def beneficiary(beneficiary_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.beneficiaries WHERE id = %s", (beneficiary_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "không có người thụ hưởng")
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "holder_name": row["holder_name"],
        "saved_at": row["saved_at"].isoformat(),
        "times_paid": int(row["times_paid"]),
    }


def _next_id(conn, prefix: str) -> str:
    row = conn.execute("SELECT count(*) AS n FROM bank.transfers").fetchone()
    return f"{prefix}-{int(row['n']) + 1}"


@app.post("/transfer")
def transfer(body: TransferIn) -> dict:
    now = CLOCK.now()
    session = touch_session(body.from_account, body.device, now)
    with connect() as conn:
        debit(conn, body.from_account, body.amount)
        balance = conn.execute("SELECT balance, segment, has_guardian FROM bank.accounts WHERE id = %s", (body.from_account,)).fetchone()
        transfer_id = _next_id(conn, "X")
        decision_id = f"dec-{transfer_id}"
        decided = engine_post(
            "/decide",
            {
                "decision_id": decision_id,
                "profile": "advanced",
                "sender_hash": account_hash(body.from_account),
                "payee_hash": account_hash(body.to_account),
                "amount": body.amount,
                "balance": int(balance["balance"]) + body.amount,
                "ts": now.isoformat(),
                "session_id": session,
                "score_request": True,
                "device": body.device,
            },
        )
        level = decided["level"]
        funds = decided["funds_status"]
        hung = False
        status = status_of(level, funds, hung)
        conn.execute(
            """
            INSERT INTO bank.transfers
            (id, from_account, to_account, amount, purpose_text, decision_id, level, funds_status, status,
             hold_until, countdown_until, gate, hung_up, posted, actions, reasons, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,%s::jsonb,%s::jsonb,%s)
            """,
            (
                transfer_id,
                body.from_account,
                body.to_account,
                body.amount,
                body.purpose_text,
                decision_id,
                level,
                funds,
                status,
                decided.get("hold_until"),
                decided.get("countdown_until"),
                "none",
                json.dumps(decided.get("actions") or []),
                json.dumps(decided.get("reasons") or []),
                now,
            ),
        )
        entry(conn, body.from_account, transfer_id, now, "out", body.amount, "transfer", body.purpose_text or "", status)
        stored = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (transfer_id,)).fetchone()
        if funds == "released":
            release_payee(conn, stored, now)
        if level == "medium" and balance["has_guardian"]:
            conn.execute(
                """
                INSERT INTO bank.guardian_requests (id, account_id, transfer_id, level, funds_status, status)
                VALUES (%s,%s,%s,%s,%s,'open')
                """,
                (f"G-{transfer_id}", body.from_account, transfer_id, level, funds),
            )
        conn.commit()
    return {
        "transfer_id": transfer_id,
        "status": status,
        "funds_status": funds,
        "hold_until": decided.get("hold_until"),
        "decision": {"level": level, "actions": decided.get("actions") or [], "reasons": decided.get("reasons") or []},
    }


@app.post("/transfer/{transfer_id}/respond")
def respond(transfer_id: str, body: RespondIn) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (transfer_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, "không có lệnh")
        payload = {key: value for key, value in body.model_dump().items() if value is not None}
        payload["decision_id"] = transfer["decision_id"]
        payload["ts"] = now.isoformat()
        answered = engine_post("/respond", payload)
        previous = transfer["funds_status"]
        funds = answered["funds_status"]
        level = answered["level"]
        hung = bool(transfer["hung_up"] or body.hung_up)
        status = status_of(level, funds, hung)
        apply_funds(conn, transfer, previous, funds, now)
        conn.execute(
            """
            UPDATE bank.transfers
            SET level=%s, funds_status=%s, status=%s, hold_until=%s, countdown_until=%s, gate=%s, hung_up=%s
            WHERE id=%s
            """,
            (level, funds, status, answered.get("hold_until"), answered.get("countdown_until"), answered.get("gate"), hung, transfer_id),
        )
        conn.commit()
    return {
        "transfer_id": transfer_id,
        "status": status,
        "level": level,
        "funds_status": funds,
        "gate": answered.get("gate"),
        "hold_until": answered.get("hold_until"),
        "countdown_until": answered.get("countdown_until"),
    }


def _product(body: ProductIn, kind: str, tx_type: str) -> dict:
    now = CLOCK.now()
    touch_session(body.account_id, body.device, now)
    with connect() as conn:
        row = conn.execute("SELECT count(*) AS n FROM bank.products").fetchone()
        product_id = f"P-{int(row['n']) + 1}"
        credit(conn, body.account_id, body.amount)
        entry(conn, body.account_id, product_id, now, "in", body.amount, tx_type, kind, "done")
        conn.execute(
            "INSERT INTO bank.products (id, account_id, amount, kind, ts) VALUES (%s,%s,%s,%s,%s)",
            (product_id, body.account_id, body.amount, kind, now),
        )
        conn.commit()
    post_ledger(
        {
            "event_id": f"product:{product_id}",
            "type": "product",
            "ts": now.isoformat(),
            "account_hash": account_hash(body.account_id),
            "product_type": tx_type,
            "tx_type": tx_type,
            "amount": body.amount,
            "session_id": f"demo-{body.account_id}",
        }
    )
    return {"id": product_id, "status": "done"}


@app.post("/savings/liquidate")
def liquidate(body: ProductIn) -> dict:
    return _product(body, "savings_liquidation", "savings_liquidation")


@app.post("/loan")
def loan(body: ProductIn) -> dict:
    return _product(body, "quick_loan_disbursement", "quick_loan_disbursement")


@app.post("/card/cash-advance")
def cash_advance(body: ProductIn) -> dict:
    return _product(body, "credit_card_cash_advance", "credit_card_cash_advance")


@app.post("/limit")
def limit(body: LimitIn) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        row = conn.execute("UPDATE bank.accounts SET limit_per_txn=%s, limit_per_day=%s WHERE id=%s RETURNING id", (body.limit_per_txn, body.limit_per_day, body.account_id)).fetchone()
        if row is None:
            raise HTTPException(404, "không có tài khoản")
        count = conn.execute("SELECT count(*) AS n FROM bank.products").fetchone()
        change_id = f"L-{int(count['n']) + 1}"
        conn.execute(
            "INSERT INTO bank.products (id, account_id, amount, kind, ts) VALUES (%s,%s,%s,'limit_raise',%s)",
            (change_id, body.account_id, body.limit_per_txn, now),
        )
        conn.commit()
    post_ledger(
        {
            "event_id": f"account_change:{change_id}",
            "type": "account_change",
            "ts": now.isoformat(),
            "account_hash": account_hash(body.account_id),
            "change_type": "limit_raise",
        }
    )
    return {"id": change_id, "status": "done"}


@app.post("/guardian")
def guardian(body: GuardianIn) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (body.account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        conn.execute(
            """
            INSERT INTO bank.guardians (account_id, guardian_phone_hash, relation, registered_at)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (account_id) DO UPDATE SET guardian_phone_hash=EXCLUDED.guardian_phone_hash, relation=EXCLUDED.relation
            """,
            (body.account_id, body.guardian_phone_hash, body.relation, now),
        )
        conn.execute("UPDATE bank.accounts SET has_guardian = true WHERE id = %s", (body.account_id,))
        conn.commit()
    return {"account_id": body.account_id, "status": "registered"}


@app.get("/guardian/requests")
def guardian_requests() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM bank.guardian_requests WHERE status = 'open' ORDER BY id").fetchall()
    return {
        "requests": [
            {"id": row["id"], "account_id": row["account_id"], "transfer_id": row["transfer_id"], "level": row["level"], "funds_status": row["funds_status"]}
            for row in rows
        ]
    }


@app.post("/guardian/requests/{request_id}/confirm")
def confirm_guardian(request_id: str) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.guardian_requests WHERE id = %s", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "không có yêu cầu")
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (row["transfer_id"],)).fetchone()
        answered = engine_post("/respond", {"decision_id": transfer["decision_id"], "guardian_confirm": True, "ts": now.isoformat()})
        apply_funds(conn, transfer, transfer["funds_status"], answered["funds_status"], now)
        conn.execute(
            "UPDATE bank.transfers SET funds_status=%s, status=%s, level=%s WHERE id=%s",
            (answered["funds_status"], status_of(answered["level"], answered["funds_status"], transfer["hung_up"]), answered["level"], transfer["id"]),
        )
        conn.execute("UPDATE bank.guardian_requests SET status='confirmed', funds_status='released' WHERE id=%s", (request_id,))
        conn.commit()
    return {"id": request_id, "funds_status": "released"}


if __name__ == "__main__":
    import uvicorn

    require_key()
    uvicorn.run(app, host="0.0.0.0", port=8080)
