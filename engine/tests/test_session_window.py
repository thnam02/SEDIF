"""a_w và new_payees_24h chỉ tính tài khoản của phiên. Redis db 14, không đụng db 15."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")

from engine.features.online import DAY_US, OnlineStore

SENDER = "a" * 64
SIBLING = "b" * 64
PAYEE_A = "c" * 64
PAYEE_B = "d" * 64
CUSTOMER = "C-window"


def test_window_ignores_the_customers_other_account():
    store = OnlineStore("redis://localhost:6379/14", flush=True)
    try:
        start = 1_700_000_000_000_000
        for account in (SENDER, SIBLING):
            store.update(
                {
                    "type": "account",
                    "account": account,
                    "opened": start - 400 * DAY_US,
                    "holder": "Nam",
                    "merchant": 0,
                    "b01": 1,
                    "customer": CUSTOMER,
                    "segment": "thường",
                    "has_guardian": 0,
                    "limit": 50_000_000,
                }
            )
        store.update(
            {
                "type": "session",
                "session": "S-window",
                "account": SENDER,
                "start": start,
                "end": start + 3_600_000_000,
                "customer": CUSTOMER,
                "first_input": "typed",
            }
        )
        store.update(
            {
                "type": "txn",
                "ts": start + 1_000_000,
                "account": SIBLING,
                "payee": PAYEE_B,
                "known_payee": True,
                "amount": 335_000,
                "balance": 1_000_000,
                "session": "S-sibling",
                "tx_type": "transfer",
                "xfer": True,
            }
        )
        store.update(
            {
                "type": "txn",
                "ts": start + 2_000_000,
                "account": SENDER,
                "payee": PAYEE_A,
                "known_payee": True,
                "amount": 28_000,
                "balance": 1_000_000,
                "session": "S-window",
                "tx_type": "transfer",
                "xfer": True,
            }
        )
        store.flush()
        row = store.get(SENDER, PAYEE_A, start + 3_600_000_000, "S-window")
        assert row["a_w"] == 28_000
        assert row["new_payees_24h"] == 1
    finally:
        store.redis.flushdb()
