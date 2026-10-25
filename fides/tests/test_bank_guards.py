"""Giám hộ, countdown, hoàn tiền, hạn mức và Idempotency-Key."""

from __future__ import annotations

import json
import os
import uuid
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(REPO / "bank-core"), str(REPO)]
os.environ["FIDES_HMAC_KEY"] = "fides-demo-hmac"
os.environ["FIDES_API_KEY"] = "fides-demo-key"
os.environ["DATABASE_URL"] = "postgresql://hainamtran@localhost:5432/fides_w1"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["ENGINE_URL"] = "http://127.0.0.1:8094"
os.environ["SIMO_URL"] = "http://127.0.0.1:8093"
os.environ["DEMO_REPLAY"] = "0"
os.environ["DEMO_CLOCK_SPEED"] = "60"

from engine.app import Handler
from engine.common.hashing import account_hash, reset_key_cache
from engine.features.online import OnlineStore
from engine.model import Scorer
import engine.app as appmod

BANK = "http://127.0.0.1:8095"
ENGINE = "http://127.0.0.1:8094"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _call(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def _ok(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    status, payload = _call(method, url, body, headers)
    assert status == 200, payload
    return payload


def _advance(seconds: float) -> None:
    _ok("POST", f"{BANK}/clock/resume")
    time.sleep(seconds / 60 + 0.05)
    _ok("POST", f"{BANK}/clock/pause")


@pytest.fixture(scope="module")
def stack():
    reset_key_cache()
    appmod.STORE = OnlineStore(flush=False)
    appmod.MODEL = Scorer.load()
    engine = ThreadingHTTPServer(("127.0.0.1", 8094), Handler)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    bank = _load("bank_core_guards", REPO / "bank-core" / "app.py")
    import uvicorn

    threading.Thread(target=lambda: uvicorn.run(bank.app, host="127.0.0.1", port=8095, log_level="warning"), daemon=True).start()
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BANK}/health", timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.4)
    else:
        raise AssertionError("bank-core không lên")
    holder = {"engine": engine}
    yield holder
    holder["engine"].shutdown()


def test_lists_saved_beneficiaries_and_savings(stack):
    listed = _ok("GET", f"{BANK}/accounts/A0002119/beneficiaries")
    assert listed["account_id"] == "A0002119"
    assert listed["beneficiaries"]
    assert listed["beneficiaries"][0]["times_paid"] >= listed["beneficiaries"][-1]["times_paid"]
    for row in listed["beneficiaries"]:
        assert {"id", "account_id", "holder_name", "saved_at", "times_paid"} <= set(row)
    status, payload = _call("GET", f"{BANK}/accounts/ZMISSING/beneficiaries")
    assert status == 404 and "không có tài khoản" in str(payload.get("detail"))
    books = _ok("GET", f"{BANK}/accounts/A0000623/savings")
    assert books["account_id"] == "A0000623"
    assert any(book["id"] == "A0077690" and book["balance"] > 0 for book in books["books"])
    account = _ok("GET", f"{BANK}/accounts/A0002119")
    assert account["account_type"] == "personal"


def test_limit_and_balance_are_rejected(stack):
    account = "A0031239"
    balance = int(_ok("GET", f"{BANK}/accounts/{account}")["balance"])
    status, payload = _call("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": balance + 1})
    assert status == 400 and "không đủ số dư" in str(payload.get("detail"))
    _ok("POST", f"{BANK}/limit", {"account_id": account, "limit_per_txn": 1_000, "limit_per_day": 5_000})
    status, payload = _call("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": 2_000})
    assert status == 400 and "vượt hạn mức" in str(payload.get("detail"))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE bank.accounts SET balance = balance + 20000 WHERE id = %s", (account,))
        conn.commit()
    first = _ok("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": 1_000})
    assert first["transfer_id"]
    _ok("POST", f"{BANK}/limit", {"account_id": account, "limit_per_txn": 10_000, "limit_per_day": 1_500})
    status, payload = _call("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": 1_000})
    assert status == 400 and "vượt hạn mức ngày" in str(payload.get("detail"))


def test_transfer_is_idempotent(stack):
    account = "A0031239"
    _ok("POST", f"{BANK}/limit", {"account_id": account, "limit_per_txn": 1_000_000_000, "limit_per_day": 1_000_000_000})
    before = int(_ok("GET", f"{BANK}/accounts/{account}")["balance"])
    headers = {"Idempotency-Key": "fee-once"}
    body = {"from_account": account, "to_account": "A0002119", "amount": 1_000, "purpose_text": "một lần"}
    first = _ok("POST", f"{BANK}/transfer", body, headers)
    second = _ok("POST", f"{BANK}/transfer", body, headers)
    after = int(_ok("GET", f"{BANK}/accounts/{account}")["balance"])
    assert first["transfer_id"] == second["transfer_id"]
    assert before - after == 1_000


def test_z_cancel_during_hold_refunds(stack):
    sender = "A0050272"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE bank.accounts SET balance = balance + 300000000 WHERE id = %s", (sender,))
        conn.commit()
    device = {"in_call": True, "acct_input": "pasted"}
    created = _ok(
        "POST",
        f"{BANK}/transfer",
        {"from_account": sender, "to_account": "ZCANCEL1", "amount": 40_000_000, "purpose_text": "giữ", "device": device},
    )
    assert created["funds_status"] == "held", created
    before = int(_ok("GET", f"{BANK}/accounts/{sender}")["balance"])
    cancelled = _ok("POST", f"{BANK}/transfer/{created['transfer_id']}/respond", {"cancel": True})
    after = int(_ok("GET", f"{BANK}/accounts/{sender}")["balance"])
    assert cancelled["status"] == "cancelled"
    assert after == before + 40_000_000


def test_z_new_call_resets_countdown(stack):
    sender = "A0050272"
    device = {"in_call": True, "acct_input": "pasted"}
    created = _ok(
        "POST",
        f"{BANK}/transfer",
        {"from_account": sender, "to_account": "ZCOUNT1", "amount": 40_000_000, "purpose_text": "cúp", "device": device},
    )
    assert created["decision"]["level"] == "high", created
    hung = _ok("POST", f"{BANK}/transfer/{created['transfer_id']}/respond", {"hung_up": True})
    first = datetime.fromisoformat(hung["countdown_until"])
    _advance(30)
    stamp = datetime.fromisoformat(_ok("GET", f"{BANK}/clock")["ts"])
    status, payload = _call(
        "POST",
        f"{ENGINE}/events",
        {
            "type": "device_signal",
            "ts": int(stamp.timestamp() * 1_000_000),
            "session": f"demo-{sender}",
            "in_call": "1",
            "ended": "0",
        },
        {"X-API-Key": os.environ["FIDES_API_KEY"]},
    )
    assert status in {200, 201}, payload
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            """
            SELECT countdown_until FROM responses
            WHERE decision_id = %s ORDER BY id DESC LIMIT 1
            """,
            (f"dec-{created['transfer_id']}",),
        ).fetchone()
    assert row[0] > first
    assert abs((row[0] - stamp).total_seconds() - 600) < 2


def test_guardian_confirm_releases(stack):
    sender = "A0072556"
    payee = "Z" + uuid.uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE bank.accounts SET has_guardian = true WHERE id = %s", (sender,))
        conn.execute(
            """
            INSERT INTO bank.accounts
            (id, customer_id, holder_name, segment, balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
            VALUES (%s,'CDEMO','Nguoi nha','thường',0,'open',false,'personal','B02',1000000000,1000000000)
            ON CONFLICT (id) DO NOTHING
            """,
            (payee,),
        )
        conn.commit()
    _ok("POST", f"{BANK}/savings/liquidate", {"account_id": sender, "amount": 40_000_000})
    before = int(_ok("GET", f"{BANK}/accounts/{payee}")["balance"])
    created = _ok(
        "POST",
        f"{BANK}/transfer",
        {"from_account": sender, "to_account": payee, "amount": 30_000_000, "purpose_text": "nhà", "device": {"acct_input": "typed"}},
    )
    assert created["decision"]["level"] == "medium", created
    queued = _ok("GET", f"{BANK}/guardian/requests")
    match = [row for row in queued["requests"] if row["transfer_id"] == created["transfer_id"]]
    assert match and match[0]["funds_status"] == "held"
    confirmed = _ok("POST", f"{BANK}/guardian/requests/{match[0]['id']}/confirm")
    assert confirmed["funds_status"] == "released"
    after = int(_ok("GET", f"{BANK}/accounts/{payee}")["balance"])
    assert after == before + 30_000_000


def test_basic_profile_without_device(stack):
    account = "A0078375"
    _ok("POST", f"{BANK}/limit", {"account_id": account, "limit_per_txn": 1_000_000_000, "limit_per_day": 1_000_000_000})
    created = _ok("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": 1_000})
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute("SELECT profile, payload FROM decisions WHERE decision_id = %s", (f"dec-{created['transfer_id']}",)).fetchone()
    assert row["profile"] == "basic"
    assert row["payload"]["profile"] == "basic"
    sys.path.insert(0, str(ROOT))
    from features.store import unread_signals

    assert row["payload"]["missing_signals"] == unread_signals("basic", "network")


def test_z_engine_down_transfers_then_recovers(stack):
    account = "A0097589"
    payee = "ZFALLBACK1"
    _ok("POST", f"{BANK}/limit", {"account_id": account, "limit_per_txn": 1_000_000_000, "limit_per_day": 1_000_000_000})
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE bank.accounts SET balance = balance + 80000000 WHERE id = %s", (account,))
        conn.execute(
            """
            INSERT INTO bank.accounts
            (id, customer_id, holder_name, segment, balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
            VALUES (%s,'CDEMO','Nguoi moi','thường',0,'open',false,'personal','B02',1000000000,1000000000)
            ON CONFLICT (id) DO NOTHING
            """,
            (payee,),
        )
        conn.commit()
    stack["engine"].shutdown()
    stack["engine"].server_close()
    small = _ok("POST", f"{BANK}/transfer", {"from_account": account, "to_account": "A0002119", "amount": 1_000})
    assert small["status"] == "done" and small["decision"]["level"] == "none" and small["funds_status"] == "released"
    warned = _ok("POST", f"{BANK}/transfer", {"from_account": account, "to_account": payee, "amount": 50_000_000})
    assert warned["decision"]["level"] == "banner" and warned["status"] == "done", warned
    assert warned["decision"]["reasons"][0]["code"] == "new_payee"
    restarted = ThreadingHTTPServer(("127.0.0.1", 8094), Handler)
    threading.Thread(target=restarted.serve_forever, daemon=True).start()
    stack["engine"] = restarted
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{ENGINE}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    restored = _ok(
        "POST",
        f"{BANK}/transfer",
        {"from_account": account, "to_account": "A0002119", "amount": 1_000, "device": {"acct_input": "typed"}},
    )
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute("SELECT profile FROM decisions WHERE decision_id = %s", (f"dec-{restored['transfer_id']}",)).fetchone()
    assert row["profile"] == "advanced"
