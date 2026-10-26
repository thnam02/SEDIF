#!/usr/bin/env python3
"""Chỉ đọc. So sổ bank-core, events Postgres và Redis từ 2026-09-20 tới đồng hồ demo.

Không ghi Postgres, không ghi Redis, không nạp lại sổ.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
from pathlib import Path

import pandas as pd
import psycopg
import redis

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "fides"), str(REPO / "bank-core")]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from engine.common.hashing import account_hash, reset_key_cache
from engine.features.online import PRODUCT_TX, OnlineStore
from features.ledger import COMPARED, ledger_pair_features
from load import CUT, OBSERVED, PERSONAS

BANK = os.environ.get("BANK_URL", "http://127.0.0.1:8082")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://fides:fides@127.0.0.1:5432/fides")
TYPES = ("txn", "product", "account_change")
DAY_US = 86_400 * 1_000_000


def _clock() -> pd.Timestamp:
    with urllib.request.urlopen(f"{BANK}/clock", timeout=10) as response:
        payload = json.loads(response.read().decode())
    return pd.Timestamp(payload["ts"])


def _epoch_ns(series: pd.Series) -> pd.Series:
    values = pd.to_datetime(series, utc=True)
    if getattr(values.dtype, "unit", "ns") != "ns":
        values = values.dt.as_unit("ns")
    return values.astype("int64")


def _us(stamp: pd.Timestamp) -> int:
    return int(stamp.timestamp() * 1_000_000)


def _ns(stamp: pd.Timestamp) -> int:
    return int(stamp.value)


def _bank_events(conn, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT 'txn:' || id AS event_id, 'txn' AS type, from_account AS account_id, to_account, created_at AS ts
        FROM bank.transfers
        WHERE posted AND created_at >= %s AND created_at <= %s
        UNION ALL
        SELECT 'product:' || id, 'product', account_id, NULL, ts
        FROM bank.products
        WHERE kind <> 'limit_raise' AND ts >= %s AND ts <= %s
        UNION ALL
        SELECT 'account_change:' || id, 'account_change', account_id, NULL, ts
        FROM bank.products
        WHERE kind = 'limit_raise' AND ts >= %s AND ts <= %s
        """,
        (start, end, start, end, start, end),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["event_id", "type", "account_id", "to_account", "ts"])
    if frame.empty:
        return frame
    frame["account_hash"] = frame["account_id"].map(lambda item: account_hash(str(item)))
    return frame


def _engine_events(conn, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT event_id, type, account_hash
        FROM events
        WHERE ts >= %s AND ts <= %s AND type = ANY(%s)
        """,
        (start, end, list(TYPES)),
    ).fetchall()
    return pd.DataFrame(rows, columns=["event_id", "type", "account_hash"])


def _counts(frame: pd.DataFrame, column: str = "type") -> dict[str, int]:
    if frame.empty:
        return {kind: 0 for kind in TYPES}
    found = frame[column].value_counts().to_dict()
    return {kind: int(found.get(kind, 0)) for kind in TYPES}


def _redis_counts(client, account: str, start_us: int, end_us: int) -> dict[str, int]:
    """Chỉ sự kiện bank-core vừa ghi, phiên demo-. Snapshot dân số nằm sẵn trong RDB."""
    members = client.zrangebyscore(f"f:out:{account}", start_us, end_us)
    txn = 0
    product = 0
    for member in members:
        parts = str(member).split("|")
        if len(parts) < 8 or not parts[6].startswith("demo-"):
            continue
        if parts[-1] in PRODUCT_TX:
            product += 1
        else:
            txn += 1
    return {"txn": txn, "product": product, "account_change": 0}


def _print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"{label} " + " ".join(f"{kind}={counts[kind]}" for kind in TYPES))


def _pairs(conn, personas: list[str]) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT owner_account, account_id
        FROM bank.beneficiaries
        WHERE owner_account = ANY(%s)
        ORDER BY owner_account, account_id
        """,
        (personas,),
    ).fetchall()
    chosen: list[tuple[str, str]] = []
    seen_sender: set[str] = set()
    for owner, payee in rows:
        if owner not in seen_sender:
            chosen.append((str(owner), str(payee)))
            seen_sender.add(str(owner))
    for owner, payee in rows:
        if len(chosen) >= 50:
            break
        pair = (str(owner), str(payee))
        if pair not in chosen:
            chosen.append(pair)
    if len(chosen) < 50 and OBSERVED.joinpath("transactions.parquet").exists():
        tx = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["from_account", "to_account", "ts"])
        tx = tx.loc[tx["from_account"].astype(str).isin(personas) & tx["to_account"].notna()]
        for record in tx.itertuples(index=False):
            if len(chosen) >= 50:
                break
            pair = (str(record.from_account), str(record.to_account))
            if pair not in chosen:
                chosen.append(pair)
    return chosen[:50]


def _ledger(conn, as_of: pd.Timestamp, accounts: set[str]):
    customers = conn.execute(
        "SELECT id, customer_id FROM bank.accounts WHERE id = ANY(%s) OR customer_id IN (SELECT customer_id FROM bank.accounts WHERE id = ANY(%s))",
        (list(accounts), list(accounts)),
    ).fetchall()
    owned = {str(row[0]) for row in customers}
    customer_of = {str(row[0]): str(row[1] or "") for row in customers}
    history = pd.read_parquet(
        OBSERVED / "transactions.parquet",
        columns=["from_account", "to_account", "amount", "tx_type", "ts"],
    )
    history["from_account"] = history["from_account"].astype(str)
    history["to_account"] = history["to_account"].astype(str)
    history["ts"] = pd.to_datetime(history["ts"])
    history = history.loc[(history["ts"] < as_of) & history["from_account"].isin(owned)]
    live = conn.execute(
        """
        SELECT id, from_account, to_account, amount, created_at
        FROM bank.transfers
        WHERE posted AND created_at >= %s AND created_at <= %s AND from_account = ANY(%s)
        """,
        (CUT.to_pydatetime(), as_of.to_pydatetime(), list(owned)),
    ).fetchall()
    posted = {f"txn:{row[0]}": row for row in live}
    stamped = {}
    if posted:
        stamped = dict(conn.execute("SELECT event_id, ts FROM events WHERE event_id = ANY(%s)", (list(posted),)).fetchall())
    extra = pd.DataFrame(
        [
            (row[1], row[2], row[3], stamped.get(f"txn:{row[0]}", row[4]))
            for row in live
        ],
        columns=["from_account", "to_account", "amount", "ts"],
    )
    if not extra.empty:
        extra["tx_type"] = "transfer"
        extra["ts"] = pd.to_datetime(extra["ts"])
    transfers = pd.concat([history, extra], ignore_index=True) if not extra.empty else history
    transfers["amount"] = transfers["amount"].fillna(0).astype("int64")
    transfers["ts_ns"] = _epoch_ns(transfers["ts"])

    products_live = conn.execute(
        """
        SELECT id, account_id, kind, ts FROM bank.products
        WHERE ts >= %s AND ts <= %s AND account_id = ANY(%s) AND kind <> 'limit_raise'
        """,
        (CUT.to_pydatetime(), as_of.to_pydatetime(), list(owned)),
    ).fetchall()
    product_ids = [f"product:{row[0]}" for row in products_live]
    product_ts = {}
    if product_ids:
        product_ts = dict(conn.execute("SELECT event_id, ts FROM events WHERE event_id = ANY(%s)", (product_ids,)).fetchall())
    products = pd.DataFrame(
        [(row[1], row[2], product_ts.get(f"product:{row[0]}", row[3])) for row in products_live],
        columns=["account_id", "kind", "ts"],
    )
    old = history.loc[history["tx_type"].isin(PRODUCT_TX), ["from_account", "tx_type", "ts"]].rename(
        columns={"from_account": "account_id", "tx_type": "kind"}
    )
    products = pd.concat([old, products], ignore_index=True) if not products.empty else old
    if products.empty:
        products = pd.DataFrame(columns=["account_id", "kind", "ts", "ts_ns"])
    else:
        products["ts_ns"] = _epoch_ns(products["ts"])

    raises_live = conn.execute(
        """
        SELECT id, account_id, ts FROM bank.products
        WHERE kind = 'limit_raise' AND ts >= %s AND ts <= %s AND account_id = ANY(%s)
        """,
        (CUT.to_pydatetime(), as_of.to_pydatetime(), list(owned)),
    ).fetchall()
    raise_ids = [f"account_change:{row[0]}" for row in raises_live]
    raise_ts = {}
    if raise_ids:
        raise_ts = dict(conn.execute("SELECT event_id, ts FROM events WHERE event_id = ANY(%s)", (raise_ids,)).fetchall())
    raises = pd.DataFrame(
        [(row[1], raise_ts.get(f"account_change:{row[0]}", row[2])) for row in raises_live],
        columns=["account_id", "ts"],
    )
    changes = pd.read_parquet(OBSERVED / "account_changes.parquet", columns=["account_id", "ts", "change_type"])
    changes["account_id"] = changes["account_id"].astype(str)
    changes["ts"] = pd.to_datetime(changes["ts"])
    changes = changes.loc[(changes["ts"] < as_of) & (changes["change_type"] == "limit_raise") & changes["account_id"].isin(owned)]
    old_raises = changes[["account_id", "ts"]]
    raises = pd.concat([old_raises, raises], ignore_index=True) if not raises.empty else old_raises
    if raises.empty:
        raises = pd.DataFrame(columns=["account_id", "ts", "ts_ns"])
    else:
        raises["ts_ns"] = _epoch_ns(raises["ts"])
    groups = {}
    for account, customer in customer_of.items():
        groups.setdefault(customer or account, set()).add(account)
    return transfers, products, raises, groups


def main() -> int:
    reset_key_cache()
    end = _clock()
    start = pd.Timestamp(CUT)
    start_us, end_us = _us(start), _us(end)
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("SET default_transaction_read_only = on")
        bank = _bank_events(conn, start, end)
        engine = _engine_events(conn, start, end)
        personas = [item for person in json.loads(PERSONAS.read_text(encoding="utf-8"))["personas"] for item in person["account_ids"]]
        pairs = _pairs(conn, personas)
        senders = {sender for sender, _payee in pairs}
        transfers, products, raises, groups = _ledger(conn, end, senders | set(personas))
    simo = pd.read_parquet(OBSERVED / "simo_list.parquet", columns=["account_id", "listed_at"])
    simo["account_id"] = simo["account_id"].astype(str)
    simo_listed = dict(zip(simo["account_id"].astype(str), _epoch_ns(simo["listed_at"]).astype(int)))
    bank_counts = _counts(bank)
    engine_counts = _counts(engine)
    bank_ids = set(bank["event_id"]) if not bank.empty else set()
    engine_ids = set(engine["event_id"]) if not engine.empty else set()
    only_engine = sorted(engine_ids - bank_ids)
    only_bank = sorted(bank_ids - engine_ids)
    hashes = set(personas)
    if not bank.empty:
        hashes.update(bank["account_id"].astype(str))
    hashed = {account_hash(item): item for item in hashes}
    if not engine.empty:
        hashed.update({item: hashed.get(item, item) for item in engine["account_hash"].astype(str)})
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    store = OnlineStore(flush=False)
    store.flush = lambda: None
    redis_total = {kind: 0 for kind in TYPES}
    mismatches = len(only_engine) + len(only_bank)
    per_hash_bank = {} if bank.empty else bank.groupby(["account_hash", "type"]).size().to_dict()
    per_hash_engine = {} if engine.empty else engine.groupby(["account_hash", "type"]).size().to_dict()
    engine_hashes = set(engine["account_hash"].astype(str)) if not engine.empty else set()
    for hashed_account in sorted(set(hashed) | engine_hashes):
        counted = _redis_counts(client, hashed_account, start_us, end_us)
        for kind in TYPES:
            redis_total[kind] += counted[kind]
            left = int(per_hash_bank.get((hashed_account, kind), 0))
            middle = int(per_hash_engine.get((hashed_account, kind), 0))
            if kind == "account_change":
                if left != middle:
                    mismatches += 1
                    print(f"redis {hashed_account} {kind} bank={left} engine={middle} redis={counted[kind]}")
                continue
            if counted[kind] != left or counted[kind] != middle:
                mismatches += 1
                print(f"redis {hashed_account} {kind} bank={left} engine={middle} redis={counted[kind]}")
    _print_counts("bank-core", bank_counts)
    _print_counts("engine", engine_counts)
    _print_counts("redis", redis_total)
    print("engine_only " + (", ".join(only_engine) if only_engine else "-"))
    print("bank_only " + (", ".join(only_bank) if only_bank else "-"))
    as_of_ns = _ns(end)
    as_of_us = end_us
    feature_gaps = 0
    for sender, payee in pairs:
        customer_accounts = sorted(groups.get(next((key for key, vals in groups.items() if sender in vals), sender), {sender}))
        offline = ledger_pair_features(sender, payee, as_of_ns, customer_accounts, transfers, products, raises, simo_listed)
        online = store._read(
            account_hash(sender),
            account_hash(payee),
            as_of_us,
            "check",
            {"ts": as_of_us, "payee": account_hash(payee), "amount": 0, "balance": 1},
        )
        for column in COMPARED:
            left = offline[column]
            right = online.get(column)
            same = right is not None and int(left) == int(right)
            if not same:
                feature_gaps += 1
                if feature_gaps <= 12:
                    print(f"{sender} {payee} {column} bank={left} engine={right}")
    if feature_gaps:
        mismatches += feature_gaps
        print(f"features {feature_gaps}")
    if mismatches:
        print(f"MISMATCH {mismatches}")
        return 1
    print("CONSISTENT")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"MISMATCH {exc}")
        sys.exit(1)
