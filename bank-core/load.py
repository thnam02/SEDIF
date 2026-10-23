"""Nạp tài khoản, số dư tới 20/9, người thụ hưởng và giám hộ của persona cùng tài khoản liên quan."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / "fides-sim" / "out" / "observed"
PERSONAS = ROOT / "docs" / "personas.json"
CUT = pd.Timestamp("2026-09-20T00:00:00+07:00")


def _money(value) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS bank;
CREATE TABLE IF NOT EXISTS bank.accounts (
    id text PRIMARY KEY,
    customer_id text,
    holder_name text NOT NULL,
    segment text NOT NULL,
    balance bigint NOT NULL,
    status text NOT NULL,
    has_guardian boolean NOT NULL,
    account_type text,
    bank_id text,
    limit_per_txn bigint NOT NULL,
    limit_per_day bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.beneficiaries (
    id text PRIMARY KEY,
    owner_account text NOT NULL,
    account_id text NOT NULL,
    holder_name text NOT NULL,
    saved_at timestamptz NOT NULL,
    times_paid integer NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.transfers (
    id text PRIMARY KEY,
    from_account text NOT NULL,
    to_account text NOT NULL,
    amount bigint NOT NULL,
    purpose_text text,
    decision_id text,
    level text,
    funds_status text NOT NULL,
    status text NOT NULL,
    hold_until timestamptz,
    countdown_until timestamptz,
    gate text,
    hung_up boolean NOT NULL DEFAULT false,
    posted boolean NOT NULL DEFAULT false,
    actions jsonb NOT NULL,
    reasons jsonb NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.entries (
    id bigserial PRIMARY KEY,
    account_id text NOT NULL,
    tx_id text NOT NULL,
    ts timestamptz NOT NULL,
    direction text NOT NULL,
    amount bigint NOT NULL,
    tx_type text NOT NULL,
    purpose_text text,
    status text NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.guardians (
    account_id text PRIMARY KEY,
    guardian_phone_hash text NOT NULL,
    relation text NOT NULL,
    registered_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.guardian_requests (
    id text PRIMARY KEY,
    account_id text NOT NULL,
    transfer_id text,
    level text NOT NULL,
    funds_status text NOT NULL,
    status text NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.products (
    id text PRIMARY KEY,
    account_id text NOT NULL,
    amount bigint NOT NULL,
    kind text NOT NULL,
    ts timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.idempotency (
    key text PRIMARY KEY,
    transfer_id text NOT NULL,
    response jsonb NOT NULL
);
"""


def related_ids() -> set[str]:
    payload = json.loads(PERSONAS.read_text(encoding="utf-8"))
    ids = {item for person in payload["personas"] for item in person["account_ids"]}
    ids.update(item["account_id"] for item in payload["mules"])
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet", columns=["account_id", "customer_id"])
    accounts["account_id"] = accounts["account_id"].astype(str)
    accounts["customer_id"] = accounts["customer_id"].astype(str)
    customers = {row.customer_id for row in accounts.itertuples(index=False) if row.account_id in ids}
    ids.update(accounts.loc[accounts["customer_id"].isin(customers), "account_id"])
    tx = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["from_account", "to_account", "ts"])
    tx["from_account"] = tx["from_account"].astype(str)
    tx["to_account"] = tx["to_account"].astype(str)
    tx = tx.loc[pd.to_datetime(tx["ts"]) < CUT]
    touched = tx.loc[tx["from_account"].isin(ids) | tx["to_account"].isin(ids)]
    known = set(accounts["account_id"])
    ids.update(item for item in touched["from_account"] if item in known)
    ids.update(item for item in touched["to_account"] if item in known)
    return ids


def load(conn) -> int:
    conn.execute(SCHEMA)
    conn.execute(
        """
        TRUNCATE bank.entries, bank.transfers, bank.products, bank.guardian_requests,
                 bank.guardians, bank.beneficiaries, bank.accounts, bank.idempotency
        """
    )
    ids = related_ids()
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet")
    accounts["account_id"] = accounts["account_id"].astype(str)
    accounts = accounts.loc[accounts["account_id"].isin(ids)].copy()
    customers = pd.read_parquet(OBSERVED / "customers.parquet", columns=["customer_id", "segment", "has_guardian"])
    customers["customer_id"] = customers["customer_id"].astype(str)
    segment = dict(zip(customers["customer_id"], customers["segment"].astype(str)))
    guardian = dict(zip(customers["customer_id"], customers["has_guardian"].fillna(False).astype(bool)))
    tx = pd.read_parquet(
        OBSERVED / "transactions.parquet",
        columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type", "purpose_text"],
    )
    tx["from_account"] = tx["from_account"].astype(str)
    tx["to_account"] = tx["to_account"].astype(str)
    tx["ts"] = pd.to_datetime(tx["ts"])
    history = tx.loc[(tx["ts"] < CUT) & (tx["from_account"].isin(ids) | tx["to_account"].isin(ids))]
    outflow = history.groupby("from_account")["amount"].sum()
    inflow = history.groupby("to_account")["amount"].sum()
    rows = []
    for record in accounts.itertuples(index=False):
        who = str(record.customer_id) if pd.notna(record.customer_id) else ""
        balance = _money(record.opening_balance) + _money(inflow.get(record.account_id, 0)) - _money(outflow.get(record.account_id, 0))
        rows.append(
            (
                record.account_id,
                who or None,
                str(record.holder_name_ascii or record.account_id),
                segment.get(who, "thường"),
                balance,
                str(record.status) if str(record.status) in {"open", "frozen", "closed"} else "open",
                bool(guardian.get(who, False)),
                str(record.account_type),
                str(record.bank_id),
                _money(record.limit_per_txn),
                _money(record.limit_per_day),
            )
        )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bank.accounts
            (id, customer_id, holder_name, segment, balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows,
        )
    payees = pd.read_parquet(OBSERVED / "payees.parquet")
    payees["from_account"] = payees["from_account"].astype(str)
    payees["to_account"] = payees["to_account"].astype(str)
    payees = payees.loc[payees["from_account"].isin(ids) & (pd.to_datetime(payees["added_at"]) < CUT)]
    names = dict(zip(accounts["account_id"], accounts["holder_name_ascii"].astype(str)))
    paid = history.loc[history["tx_type"].astype(str).isin(["transfer", "rent", "school_fee", "large_oneoff", "own_transfer", "own_transfer_drain", "passthrough_out"])]
    times = paid.groupby(["from_account", "to_account"]).size()
    beneficiaries = []
    for index, record in enumerate(payees.itertuples(index=False)):
        beneficiaries.append(
            (
                f"BE-{index}",
                record.from_account,
                record.to_account,
                names.get(record.to_account, record.to_account),
                pd.Timestamp(record.added_at).to_pydatetime(),
                int(times.get((record.from_account, record.to_account), 0)),
            )
        )
    if beneficiaries:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO bank.beneficiaries (id, owner_account, account_id, holder_name, saved_at, times_paid)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                beneficiaries,
            )
    payload = json.loads(PERSONAS.read_text(encoding="utf-8"))
    for person in payload["personas"]:
        guard = person.get("guardian") or {}
        phone = guard.get("guardian_phone_number_id")
        if not phone:
            continue
        from engine.common.hashing import account_hash

        for account_id in person["account_ids"]:
            conn.execute(
                """
                INSERT INTO bank.guardians (account_id, guardian_phone_hash, relation, registered_at)
                VALUES (%s,%s,%s,%s) ON CONFLICT (account_id) DO NOTHING
                """,
                (account_id, account_hash(str(phone)), str(guard.get("relation") or "người_nhà"), CUT.to_pydatetime()),
            )
    conn.commit()
    return len(rows)
