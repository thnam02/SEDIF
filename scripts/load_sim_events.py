"""Đổ observed/ vào sổ events. Băm tài khoản bằng HMAC. Chỉ đọc observed/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.common.hashing import account_hash
from engine.events import connect, ensure_schema, insert_many

OBSERVED = ROOT / "fides-sim" / "out" / "observed"
PRODUCT_TX = {"savings_liquidation", "quick_loan_disbursement", "credit_card_cash_advance"}
PRODUCT_ACCOUNTS = {"savings", "securities"}
BATCH = 5000


def _iso(value) -> str:
    return pd.Timestamp(value).isoformat()


def _kept(value, until: pd.Timestamp | None) -> bool:
    if until is None or pd.isna(value):
        return until is None and not pd.isna(value)
    return pd.Timestamp(value) < until


def iter_events(observed: Path, until: pd.Timestamp | None):
    transactions = pd.read_parquet(observed / "transactions.parquet", columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type"])
    for row in transactions.itertuples(index=False):
        if not _kept(row.ts, until):
            continue
        counterparty = None if pd.isna(row.to_account) or str(row.to_account) in {"", "<NA>"} else account_hash(str(row.to_account))
        yield {
            "event_id": f"txn:{row.tx_id}",
            "type": "txn",
            "ts": _iso(row.ts),
            "account_hash": account_hash(str(row.from_account)),
            "counterparty_hash": counterparty,
            "tx_type": str(row.tx_type),
            "amount": int(row.amount),
        }
        if str(row.tx_type) in PRODUCT_TX:
            yield {
                "event_id": f"product:{row.tx_id}",
                "type": "product",
                "ts": _iso(row.ts),
                "account_hash": account_hash(str(row.from_account)),
                "product_type": str(row.tx_type),
                "amount": int(row.amount),
            }
    changes = pd.read_parquet(observed / "account_changes.parquet", columns=["account_id", "ts", "change_type", "old_value", "new_value"])
    for index, row in enumerate(changes.itertuples(index=False)):
        if not _kept(row.ts, until):
            continue
        kind = str(row.change_type)
        base = {
            "ts": _iso(row.ts),
            "account_hash": account_hash(str(row.account_id)),
        }
        if kind == "new_device":
            yield {"event_id": f"device_change:{index}", "type": "device_change", "change_type": "new_device", **base}
            continue
        if kind not in {"limit_raise", "phone_change"}:
            continue
        yield {
            "event_id": f"account_change:{index}",
            "type": "account_change",
            "change_type": kind,
            "old_value": None if pd.isna(row.old_value) else str(row.old_value),
            "new_value": None if pd.isna(row.new_value) else str(row.new_value),
            **base,
        }
    accounts = pd.read_parquet(observed / "accounts.parquet", columns=["account_id", "account_type", "opened_at", "opening_balance"])
    for row in accounts.itertuples(index=False):
        if str(row.account_type) not in PRODUCT_ACCOUNTS or not _kept(row.opened_at, until):
            continue
        yield {
            "event_id": f"product:account:{row.account_id}",
            "type": "product",
            "ts": _iso(row.opened_at),
            "account_hash": account_hash(str(row.account_id)),
            "product_type": str(row.account_type),
            "amount": int(row.opening_balance) if not pd.isna(row.opening_balance) else 0,
        }
    complaints = pd.read_parquet(observed / "internal_complaints.parquet", columns=["complaint_id", "account_id", "session_id", "complaint_ts"])
    for row in complaints.itertuples(index=False):
        if not _kept(row.complaint_ts, until):
            continue
        yield {
            "event_id": f"complaint:{row.complaint_id}",
            "type": "complaint",
            "ts": _iso(row.complaint_ts),
            "account_hash": account_hash(str(row.account_id)),
            "session_ref": str(row.session_id),
        }
    listings = pd.read_parquet(observed / "simo_list.parquet", columns=["account_id", "listed_at", "source"])
    for row in listings.itertuples(index=False):
        if not _kept(row.listed_at, until):
            continue
        stamp = _iso(row.listed_at)
        yield {
            "event_id": f"simo:{row.account_id}:{row.listed_at.value}:{row.source}",
            "type": "simo_listing",
            "ts": stamp,
            "account_hash": account_hash(str(row.account_id)),
            "listed_at": stamp,
            "source": str(row.source),
        }


def load(observed: Path, until: str | None) -> dict[str, int]:
    cutoff = pd.Timestamp(until) if until else None
    if cutoff is not None and cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("Asia/Ho_Chi_Minh")
    counts: dict[str, int] = {}
    batch: list[dict] = []
    with connect() as conn:
        ensure_schema(conn)
        for event in iter_events(observed, cutoff):
            batch.append(event)
            counts[event["type"]] = counts.get(event["type"], 0) + 1
            if len(batch) >= BATCH:
                insert_many(conn, batch)
                batch.clear()
        insert_many(conn, batch)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observed", type=Path, default=OBSERVED)
    parser.add_argument("--until", default=None, help="Chỉ ghi sự kiện có ts sớm hơn mốc này")
    args = parser.parse_args()
    counts = load(args.observed, args.until)
    for kind in sorted(counts):
        print(f"{kind} {counts[kind]}")


if __name__ == "__main__":
    main()
