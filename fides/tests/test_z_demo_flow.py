"""Phí bổ sung, countdown high, và mule B01 được báo SIMO rồi cảnh báo nạn nhân."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(REPO / "bank-core"), str(REPO)]
os.environ["FIDES_HMAC_KEY"] = "fides-demo-hmac"
os.environ["FIDES_API_KEY"] = "fides-demo-key"
os.environ["DATABASE_URL"] = "postgresql://hainamtran@localhost:5432/fides_w1"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["ENGINE_URL"] = "http://127.0.0.1:8091"
os.environ["SIMO_URL"] = "http://127.0.0.1:8093"
os.environ["DEMO_REPLAY"] = "0"
os.environ["DEMO_CLOCK_SPEED"] = "60"

import psycopg

from engine.app import Handler
from engine.common.hashing import account_hash, reset_key_cache
from engine.features.online import OnlineStore
from engine.model import Scorer
import engine.app as appmod


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _request(method: str, url: str, body: dict | None = None, key: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{method} {url} {exc.code} {exc.read().decode()}") from exc


def _wait(url: str) -> None:
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise AssertionError(f"không lên {url}")


def _advance(seconds: float) -> None:
    _request("POST", "http://127.0.0.1:8092/clock/advance", {"seconds": seconds})
    _request("POST", "http://127.0.0.1:8092/clock/pause")


def test_fee_high_and_mule_warning():
    reset_key_cache()
    appmod.STORE = OnlineStore(flush=False)
    appmod.MODEL = Scorer.load()
    engine = ThreadingHTTPServer(("127.0.0.1", 8091), Handler)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    simo = _load("simo_mock_app", REPO / "simo-mock" / "app.py")
    bank = _load("bank_core_app", REPO / "bank-core" / "app.py")
    import uvicorn

    threading.Thread(target=lambda: uvicorn.run(simo.app, host="127.0.0.1", port=8093, log_level="warning"), daemon=True).start()
    threading.Thread(target=lambda: uvicorn.run(bank.app, host="127.0.0.1", port=8092, log_level="warning"), daemon=True).start()
    _wait("http://127.0.0.1:8093/health")
    _wait("http://127.0.0.1:8092/health")
    listed = [row for row in simo.LISTED.values() if row["false_positive"]]
    assert listed, "5% liệt kê nhầm phải có trong danh sách"
    looked = _request("GET", "http://127.0.0.1:8093/simo/lookup?account_hash=" + listed[0]["account_hash"])
    assert looked["listed"] and looked["false_positive"]

    frozen = _request("GET", "http://127.0.0.1:8092/clock")
    time.sleep(0.3)
    still = _request("GET", "http://127.0.0.1:8092/clock")
    assert frozen["paused"] is False and still["ts"] > frozen["ts"] and still["ts"].startswith("2026-09-21")
    _advance(30)
    moved = _request("GET", "http://127.0.0.1:8092/clock")
    assert moved["ts"] > still["ts"] and moved["paused"]
    skipped = _request("POST", "http://127.0.0.1:8092/demo/persona", {"customer_id": "C002119"})
    assert "A0002119" in skipped["skipped_accounts"]

    sender = "A0002119"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for payee in ("ZFEE0001", "ZHIGH0001"):
            conn.execute(
                """
                INSERT INTO bank.accounts
                (id, customer_id, holder_name, segment, balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
                VALUES (%s,'CDEMO','Nguoi nhan','thường',0,'open',false,'personal','B02',1000000000,1000000000)
                ON CONFLICT (id) DO NOTHING
                """,
                (payee,),
            )
        conn.commit()
        before_payee = conn.execute("SELECT balance FROM bank.accounts WHERE id = 'ZHIGH0001'").fetchone()[0]
    device = {"in_call": True, "acct_input": "pasted"}
    done = _request("POST", "http://127.0.0.1:8092/savings/liquidate", {"account_id": sender, "amount": 80_000_000, "device": device})
    assert done["status"] == "done"
    _advance(5)
    first = _request(
        "POST",
        "http://127.0.0.1:8092/transfer",
        {"from_account": sender, "to_account": "ZFEE0001", "amount": 20_000, "purpose_text": "phi", "device": device},
    )
    assert first["status"] == "done", first
    _advance(5)
    second = _request(
        "POST",
        "http://127.0.0.1:8092/transfer",
        {"from_account": sender, "to_account": "ZHIGH0001", "amount": 50_000_000, "purpose_text": "phi bo sung", "device": device},
    )
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        first_row = conn.execute(
            "SELECT features FROM decisions WHERE decision_id = %s",
            (f"dec-{first['transfer_id']}",),
        ).fetchone()
        features = conn.execute(
            "SELECT features, p, payload FROM decisions WHERE decision_id = %s",
            (f"dec-{second['transfer_id']}",),
        ).fetchone()
    assert int(features["features"]["product_savings_7d"]) >= 1, features["features"]
    assert int(features["features"]["a_w"]) >= int(first_row["features"]["a_w"]) + 50_000_000, (first_row["features"]["a_w"], features["features"]["a_w"])
    assert "funds_mobilised" in features["payload"]["signals_present"], features["payload"]
    assert second["decision"]["level"] == 3, {
        "level": second["decision"]["level"],
        "p": features["p"],
        "reasons": second["decision"]["reasons"],
        "signals_present": features["payload"]["signals_present"],
    }
    assert second["status"] == "blocked_pending_hangup"
    held_balance = psycopg.connect(os.environ["DATABASE_URL"]).execute("SELECT balance FROM bank.accounts WHERE id = 'ZHIGH0001'").fetchone()[0]
    assert int(held_balance) == int(before_payee)

    hung = _request("POST", f"http://127.0.0.1:8092/transfer/{second['transfer_id']}/respond", {"hung_up": True})
    assert hung["status"] == "held" and hung["funds_status"] == "held"
    opened = datetime.fromisoformat(hung["countdown_until"])
    anchor = datetime.fromisoformat(_request("GET", "http://127.0.0.1:8092/clock")["ts"])
    assert abs((opened - anchor).total_seconds() - 600) < 2
    _advance(600)
    released = _request("POST", f"http://127.0.0.1:8092/transfer/{second['transfer_id']}/respond", {"proceed": True})
    assert released["funds_status"] == "released" and released["status"] == "released"
    paid = psycopg.connect(os.environ["DATABASE_URL"]).execute("SELECT balance FROM bank.accounts WHERE id = 'ZHIGH0001'").fetchone()[0]
    assert int(paid) == int(before_payee) + 50_000_000

    mule = account_hash("A0119955")
    stamp = _request("GET", "http://127.0.0.1:8092/clock")["ts"]
    for index in range(3):
        _request(
            "POST",
            "http://127.0.0.1:8091/events",
            {
                "event_id": f"txn:mule-in-{index}",
                "type": "txn",
                "ts": stamp,
                "account_hash": account_hash(f"STRANGER{index}"),
                "counterparty_hash": mule,
                "tx_type": "transfer",
                "amount": 1_000_000,
                "balance": 0,
                "session_id": f"mule-in-{index}",
            },
            key=os.environ["FIDES_API_KEY"],
        )
    _advance(30)
    outbound_ts = _request("GET", "http://127.0.0.1:8092/clock")["ts"]
    _request(
        "POST",
        "http://127.0.0.1:8091/events",
        {
            "event_id": "txn:mule-out",
            "type": "txn",
            "ts": outbound_ts,
            "account_hash": mule,
            "counterparty_hash": account_hash("CASHOUT"),
            "tx_type": "transfer",
            "amount": 10**12,
            "balance": 0,
            "session_id": "mule-out",
        },
        key=os.environ["FIDES_API_KEY"],
    )
    queue = _request("GET", "http://127.0.0.1:8091/review-queue", key=os.environ["FIDES_API_KEY"])
    match = [row for row in queue["reviews"] if row["account_hash"] == mule]
    assert match, queue
    confirmed = _request("POST", f"http://127.0.0.1:8091/review/{match[0]['review_id']}/confirm", key=os.environ["FIDES_API_KEY"])
    assert confirmed["status"] == "confirmed"
    listed_mule = _request("GET", "http://127.0.0.1:8093/simo/lookup?account_hash=" + mule)
    assert listed_mule["listed"]
    _advance(30)
    victim_account = _request("GET", "http://127.0.0.1:8092/accounts/A0031239")
    if int(victim_account["balance"]) < 10_000:
        funded = _request("POST", "http://127.0.0.1:8092/loan", {"account_id": "A0031239", "amount": 100_000})
        assert funded["status"] == "done"
        _advance(5)
    victim = _request(
        "POST",
        "http://127.0.0.1:8092/transfer",
        {"from_account": "A0031239", "to_account": "A0119955", "amount": 10_000, "purpose_text": "chuyen"},
    )
    codes = [item["code"] for item in victim["decision"]["reasons"]]
    assert "recipient_simo_flag" in codes, victim
    engine.shutdown()
