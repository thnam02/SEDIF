"""Sổ events: đếm khớp observed/, gửi lại không nhân đôi, sai khoá thì 401. Không đọc truth/."""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OBSERVED = REPO / "fides-sim" / "out" / "observed"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ["FIDES_HMAC_KEY"] = "test-hmac"
os.environ["FIDES_API_KEY"] = "test-api"
os.environ.setdefault("DATABASE_URL", "postgresql://hainamtran@localhost:5432/fides_w1")

from engine.app import Handler
from engine.common.hashing import reset_key_cache
from engine.events import connect, counts_by_type, ensure_schema
from scripts.load_sim_events import load

UNTIL = "2026-07-21T00:00:00+07:00"
PRODUCT_TX = {"savings_liquidation", "quick_loan_disbursement", "credit_card_cash_advance"}


def _until() -> pd.Timestamp:
    return pd.Timestamp(UNTIL)


def _expected() -> dict[str, int]:
    cutoff = _until()
    transactions = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["ts", "tx_type"])
    transactions = transactions.loc[transactions["ts"] < cutoff]
    changes = pd.read_parquet(OBSERVED / "account_changes.parquet", columns=["ts", "change_type"])
    changes = changes.loc[changes["ts"] < cutoff]
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet", columns=["account_type", "opened_at"])
    products = accounts.loc[accounts["account_type"].isin(["savings", "securities"]) & (accounts["opened_at"] < cutoff)]
    product_tx = transactions.loc[transactions["tx_type"].isin(PRODUCT_TX)]
    complaints = pd.read_parquet(OBSERVED / "internal_complaints.parquet", columns=["complaint_ts"])
    listings = pd.read_parquet(OBSERVED / "simo_list.parquet", columns=["listed_at"])
    kind = changes["change_type"].astype(str)
    return {
        "txn": int(len(transactions)),
        "account_change": int(kind.isin(["limit_raise", "phone_change"]).sum()),
        "device_change": int(kind.eq("new_device").sum()),
        "product": int(len(products) + len(product_tx)),
        "complaint": int((complaints["complaint_ts"] < cutoff).sum()),
        "simo_listing": int((listings["listed_at"] < cutoff).sum()),
    }


@pytest.fixture(scope="module")
def ledger():
    reset_key_cache()
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS events CASCADE")
        conn.commit()
        ensure_schema(conn)
    first = load(OBSERVED, UNTIL)
    second = load(OBSERVED, UNTIL)
    yield first, second
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS events CASCADE")
        conn.commit()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()


def _post(url: str, body: dict, key: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        url + "/events",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_loader_does_not_read_truth() -> None:
    source = (REPO / "scripts" / "load_sim_events.py").read_text(encoding="utf-8")
    assert "out/truth" not in source
    assert "import truth" not in source


def test_counts_match_observed_and_reload_is_idempotent(ledger) -> None:
    first, second = ledger
    expected = _expected()
    with connect() as conn:
        stored = counts_by_type(conn)
    assert stored == expected
    assert first == expected
    assert second == expected
    for kind in sorted(stored):
        print(f"{kind} {stored[kind]}")


def test_post_is_idempotent_and_wrong_key_is_401(server: str) -> None:
    body = {
        "event_id": "evt-test-1",
        "type": "simo_listing",
        "ts": "2026-07-01T00:00:00+07:00",
        "account_hash": "a" * 64,
        "listed_at": "2026-07-01T00:00:00+07:00",
        "source": "bank",
    }
    status, payload = _post(server, body, "test-api")
    assert status == 201 and payload["stored"] is True
    status, payload = _post(server, body, "test-api")
    assert status == 200 and payload["stored"] is False
    status, payload = _post(server, body, "sai")
    assert status == 401
    with connect() as conn:
        count = conn.execute("SELECT count(*) FROM events WHERE event_id = 'evt-test-1'").fetchone()[0]
    assert count == 1
