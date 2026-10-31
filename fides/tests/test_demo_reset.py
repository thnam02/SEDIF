"""Kịch bản phí bổ sung chạy lại giống hệt sau demo_reset.sh."""

from __future__ import annotations

import json
import os
import subprocess
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
import redis

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(REPO / "bank-core"), str(REPO)]
os.environ["FIDES_HMAC_KEY"] = "fides-demo-hmac"
os.environ["FIDES_API_KEY"] = "fides-demo-key"
os.environ["DATABASE_URL"] = "postgresql://hainamtran@localhost:5432/fides_w1"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["ENGINE_URL"] = "http://127.0.0.1:8091"
os.environ["BANK_URL"] = "http://127.0.0.1:8092"
os.environ["DEMO_REPLAY"] = "0"
os.environ["DEMO_CLOCK_SPEED"] = "60"

from engine.app import Handler
from engine.common.hashing import account_hash, reset_key_cache
from engine.features.online import OnlineStore
from engine.model import Scorer
import engine.app as appmod

BANK = os.environ["BANK_URL"]


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _request(method: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else {}


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
    _request("POST", f"{BANK}/clock/advance", {"seconds": seconds})


def _clear_demo_payees() -> None:
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    sender = account_hash("A0002119")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        customer = conn.execute("SELECT customer_id FROM bank.accounts WHERE id = 'A0002119'").fetchone()[0]
    for raw in ("ZFEE0001", "ZHIGH0001"):
        hashed = account_hash(raw)
        client.hdel(f"f:fst:{sender}", hashed)
        client.hdel(f"f:fstc:{customer}", hashed)


def _consistency() -> str:
    env = os.environ.copy()
    env["BANK_URL"] = BANK
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_consistency.py")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith(("CONSISTENT", "MISMATCH")):
        raise AssertionError(done.stdout + "\n" + done.stderr)
    return lines[-1], done.stdout


def _transfer_count() -> int:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return int(conn.execute("SELECT count(*) FROM bank.transfers").fetchone()[0])


def _reset() -> None:
    script = REPO / "scripts" / "demo_reset.sh"
    done = subprocess.run(["/bin/sh", str(script)], check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"reset {done.returncode}\n{done.stdout}\n{done.stderr}")
    assert done.stdout.strip().splitlines()[-1] == "ready"
    _clear_demo_payees()


def _fee() -> dict:
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
        before = int(conn.execute("SELECT balance FROM bank.accounts WHERE id = 'ZHIGH0001'").fetchone()[0])
    device = {"in_call": True, "acct_input": "pasted"}
    done = _request("POST", f"{BANK}/savings/liquidate", {"account_id": sender, "amount": 80_000_000, "device": device})
    assert done["status"] == "done"
    _advance(5)
    first = _request(
        "POST",
        f"{BANK}/transfer",
        {"from_account": sender, "to_account": "ZFEE0001", "amount": 20_000, "purpose_text": "phi", "device": device},
    )
    assert first["status"] == "done", first
    _advance(5)
    second = _request(
        "POST",
        f"{BANK}/transfer",
        {"from_account": sender, "to_account": "ZHIGH0001", "amount": 50_000_000, "purpose_text": "phi bo sung", "device": device},
    )
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        first_row = conn.execute("SELECT features FROM decisions WHERE decision_id = %s", (f"dec-{first['transfer_id']}",)).fetchone()
        features = conn.execute(
            "SELECT features, payload FROM decisions WHERE decision_id = %s",
            (f"dec-{second['transfer_id']}",),
        ).fetchone()
    assert int(features["features"]["product_savings_7d"]) >= 1
    assert int(features["features"]["a_w"]) >= int(first_row["features"]["a_w"]) + 50_000_000
    assert "funds_mobilised" in features["payload"]["signals_present"]
    assert second["decision"]["level"] == 3, second
    assert second["status"] == "blocked_pending_hangup"
    hung = _request("POST", f"{BANK}/transfer/{second['transfer_id']}/respond", {"hung_up": True})
    anchor = datetime.fromisoformat(_request("GET", f"{BANK}/clock")["ts"])
    opened = datetime.fromisoformat(hung["countdown_until"])
    assert abs((opened - anchor).total_seconds() - 600) < 2
    _advance(600)
    released = _request("POST", f"{BANK}/transfer/{second['transfer_id']}/respond", {"proceed": True})
    assert released["funds_status"] == "released" and released["status"] == "released"
    paid = int(psycopg.connect(os.environ["DATABASE_URL"]).execute("SELECT balance FROM bank.accounts WHERE id = 'ZHIGH0001'").fetchone()[0])
    assert paid == before + 50_000_000
    return {
        "first": first["status"],
        "level": second["decision"]["level"],
        "status": second["status"],
        "signals_present": features["payload"]["signals_present"],
        "reasons": [item["code"] for item in second["decision"]["reasons"]],
        "hung": hung["status"],
        "released": released["status"],
        "paid": paid - before,
        "savings": int(features["features"]["product_savings_7d"]),
        "a_w": int(features["features"]["a_w"]),
        "clock": _request("GET", f"{BANK}/clock")["ts"],
    }


def test_fee_scenario_repeats_after_reset():
    reset_key_cache()
    appmod.STORE = OnlineStore(flush=False)
    appmod.MODEL = Scorer.load()
    engine = ThreadingHTTPServer(("127.0.0.1", 8091), Handler)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    bank = _load("bank_core_reset", REPO / "bank-core" / "app.py")
    import uvicorn

    threading.Thread(target=lambda: uvicorn.run(bank.app, host="127.0.0.1", port=8092, log_level="warning"), daemon=True).start()
    _wait(f"{BANK}/health")
    try:
        _reset()
        first = _fee()
        verdict, report = _consistency()
        assert verdict == "CONSISTENT", report
        kept = _transfer_count()
        assert kept >= 1
        bank.boot()
        assert _transfer_count() == kept
        verdict, report = _consistency()
        assert verdict == "CONSISTENT", report
        os.environ["BANK_RESEED"] = "1"
        bank.boot()
        os.environ["BANK_RESEED"] = "0"
        bank_health = _request("GET", f"{BANK}/health")
        with urllib.request.urlopen("http://127.0.0.1:8091/health", timeout=5) as response:
            engine_health = json.loads(response.read().decode())
        assert bank_health["reset_id"] != engine_health["reset_id"]
        assert bank_health["ledger_mismatch"] is True
        blocked = urllib.request.Request(
            f"{BANK}/transfer",
            data=json.dumps({"from_account": "A0002119", "to_account": "ZHIGH0001", "amount": 1000, "purpose_text": "phi"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(blocked, timeout=30)
            raise AssertionError("transfer phải bị 409")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
            assert "ledger_mismatch" in exc.read().decode()
        verdict, report = _consistency()
        assert verdict.startswith("MISMATCH"), report
        _reset()
        verdict, report = _consistency()
        assert verdict == "CONSISTENT", report
        second = _fee()
        assert first == second
    finally:
        engine.shutdown()
