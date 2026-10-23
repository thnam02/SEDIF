"""Đổ observed/ vào sổ events. Băm tài khoản bằng HMAC. Chỉ đọc observed/."""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.common.hashing import account_hash

_hash = lru_cache(maxsize=None)(account_hash)
from engine.events import connect, ensure_schema, insert_many

OBSERVED = ROOT / "fides-sim" / "out" / "observed"
PRODUCT_TX = {"savings_liquidation", "quick_loan_disbursement", "credit_card_cash_advance"}
BATCH = 5000


def _iso(value) -> str:
    return pd.Timestamp(value).isoformat()


def _kept(value, until: pd.Timestamp | None) -> bool:
    if until is None or pd.isna(value):
        return until is None and not pd.isna(value)
    return pd.Timestamp(value) < until


def iter_events(observed: Path, until: pd.Timestamp | None):
    transactions = pd.read_parquet(
        observed / "transactions.parquet",
        columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type", "session_id", "from_balance_before"],
    )
    for row in transactions.itertuples(index=False):
        if not _kept(row.ts, until):
            continue
        counterparty = None if pd.isna(row.to_account) or str(row.to_account) in {"", "<NA>"} else _hash(str(row.to_account))
        session = None if pd.isna(row.session_id) or str(row.session_id) in {"", "<NA>"} else str(row.session_id)
        balance = 0 if pd.isna(row.from_balance_before) else int(row.from_balance_before)
        yield {
            "event_id": f"txn:{row.tx_id}",
            "type": "txn",
            "ts": _iso(row.ts),
            "account_hash": _hash(str(row.from_account)),
            "counterparty_hash": counterparty,
            "tx_type": str(row.tx_type),
            "amount": int(row.amount),
            "balance": balance,
            "session_ref": session,
        }
        if str(row.tx_type) in PRODUCT_TX:
            yield {
                "event_id": f"product:{row.tx_id}",
                "type": "product",
                "ts": _iso(row.ts),
                "account_hash": _hash(str(row.from_account)),
                "product_type": str(row.tx_type),
                "amount": int(row.amount),
                "session_ref": session,
            }
    changes = pd.read_parquet(observed / "account_changes.parquet", columns=["account_id", "ts", "change_type", "old_value", "new_value"])
    for index, row in enumerate(changes.itertuples(index=False)):
        if not _kept(row.ts, until):
            continue
        kind = str(row.change_type)
        base = {
            "ts": _iso(row.ts),
            "account_hash": _hash(str(row.account_id)),
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
    complaints = pd.read_parquet(observed / "internal_complaints.parquet", columns=["complaint_id", "account_id", "session_id", "complaint_ts"])
    for row in complaints.itertuples(index=False):
        if not _kept(row.complaint_ts, until):
            continue
        yield {
            "event_id": f"complaint:{row.complaint_id}",
            "type": "complaint",
            "ts": _iso(row.complaint_ts),
            "account_hash": _hash(str(row.account_id)),
            "session_ref": str(row.session_id),
        }
    listings = pd.read_parquet(observed / "simo_list.parquet", columns=["account_id", "listed_at", "source"])
    for index, row in enumerate(listings.itertuples(index=False)):
        if not _kept(row.listed_at, until):
            continue
        stamp = _iso(row.listed_at)
        yield {
            "event_id": f"simo:{index}",
            "type": "simo_listing",
            "ts": stamp,
            "account_hash": _hash(str(row.account_id)),
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


def source_subtypes(observed: Path, until: str) -> dict[tuple[str, str], int]:
    """Đếm bảng nguồn theo đúng cách map vào sổ. Mốc until không tính."""
    cutoff = pd.Timestamp(until)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("Asia/Ho_Chi_Minh")
    out: dict[tuple[str, str], int] = {}
    transactions = pd.read_parquet(observed / "transactions.parquet", columns=["ts", "tx_type"])
    transactions = transactions.loc[transactions["ts"] < cutoff]
    kind = transactions["tx_type"].astype(str)
    for name, count in kind.value_counts().items():
        out[("txn", str(name))] = int(count)
        if str(name) in PRODUCT_TX:
            out[("product", str(name))] = int(count)
    changes = pd.read_parquet(observed / "account_changes.parquet", columns=["ts", "change_type"])
    changes = changes.loc[changes["ts"] < cutoff]
    change_kind = changes["change_type"].astype(str)
    for name, count in change_kind.value_counts().items():
        label = "device_change" if str(name) == "new_device" else "account_change"
        if str(name) == "new_device" or str(name) in {"limit_raise", "phone_change"}:
            out[(label, str(name))] = int(count)
    complaints = pd.read_parquet(observed / "internal_complaints.parquet", columns=["complaint_ts"])
    out[("complaint", "-")] = int((complaints["complaint_ts"] < cutoff).sum())
    listings = pd.read_parquet(observed / "simo_list.parquet", columns=["listed_at", "source"])
    listings = listings.loc[listings["listed_at"] < cutoff]
    for name, count in listings["source"].astype(str).value_counts().items():
        out[("simo_listing", str(name))] = int(count)
    return out


def _print_simo_windows(observed: Path, until: str) -> None:
    cutoff = pd.Timestamp(until)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("Asia/Ho_Chi_Minh")
    start = pd.Timestamp("2026-07-01", tz="Asia/Ho_Chi_Minh")
    listings = pd.read_parquet(observed / "simo_list.parquet", columns=["listed_at"])
    ready = int((listings["listed_at"] < start).sum())
    inside = int(((listings["listed_at"] >= start) & (listings["listed_at"] < cutoff)).sum())
    print(f"simo trước sim_start {ready}")
    print(f"simo trong kỳ {inside}")


def stored_subtypes(conn) -> dict[tuple[str, str], int]:
    rows = conn.execute(
        """
        SELECT type,
               COALESCE(
                   NULLIF(body->>'tx_type', ''),
                   NULLIF(body->>'product_type', ''),
                   NULLIF(body->>'change_type', ''),
                   NULLIF(body->>'source', ''),
                   '-'
               ) AS sub,
               count(*)
        FROM events
        GROUP BY 1, 2
        """
    ).fetchall()
    return {(str(kind), str(sub)): int(count) for kind, sub, count in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observed", type=Path, default=OBSERVED)
    parser.add_argument("--until", default=None, help="Chỉ ghi sự kiện có ts sớm hơn mốc này")
    args = parser.parse_args()
    if not args.until:
        raise SystemExit("cần --until, ví dụ sim_end")
    _print_simo_windows(args.observed, args.until)
    counts = load(args.observed, args.until)
    expected = source_subtypes(args.observed, args.until)
    with connect() as conn:
        stored = stored_subtypes(conn)
    print(f"{'type':<16} {'subtype':<28} {'sổ':>10} {'nguồn':>10}")
    mismatch = 0
    for key in sorted(set(expected) | set(stored)):
        got = stored.get(key, 0)
        want = expected.get(key, 0)
        if got != want:
            mismatch += 1
        print(f"{key[0]:<16} {key[1]:<28} {got:10d} {want:10d}")
    for kind in sorted(counts):
        print(f"{kind} {counts[kind]}")
    if mismatch:
        raise SystemExit(f"{mismatch} dòng lệch nguồn")


if __name__ == "__main__":
    main()
