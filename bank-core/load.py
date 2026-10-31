"""Nạp tài khoản, số dư và lịch sử tới 20/9, người thụ hưởng và giám hộ của persona cùng tài khoản liên quan."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / "fides-sim" / "out" / "observed"
PERSONAS = ROOT / "docs" / "personas.json"
CUT = pd.Timestamp("2026-09-20T00:00:00+07:00")
HUYNH_ACCOUNT = "A0072556"
HUYNH_SAVINGS = "A0077690"
# Lương hưu trên tài khoản thanh toán. 120 triệu không đi được nếu chưa tất toán.
PENSION_BALANCE = 15_000_000
SAVINGS_PRINCIPAL = 150_000_000
# Màn hình chọn tài khoản cá nhân số dư cao nhất. Sổ kia phải thấp hơn lương hưu
# vì hạn mức 120 triệu chỉ được nâng trên A0072556.
OTHER_PERSONAL_CAP = 14_000_000


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
    opening_balance bigint NOT NULL DEFAULT 0,
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
    status text NOT NULL,
    channel text
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
    ts timestamptz NOT NULL,
    channel text
);
CREATE TABLE IF NOT EXISTS bank.idempotency (
    key text PRIMARY KEY,
    transfer_id text NOT NULL,
    response jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.call_notices (
    account_id text NOT NULL,
    shown_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.demo_state (
    id integer PRIMARY KEY,
    reset_id text NOT NULL
);
CREATE TABLE IF NOT EXISTS bank.experiment_sessions (
    id bigserial PRIMARY KEY,
    participant_id text NOT NULL,
    condition text NOT NULL,
    session_index integer NOT NULL,
    task_type text NOT NULL,
    call jsonb NOT NULL,
    behaviour_features jsonb NOT NULL,
    raw_events jsonb,
    created_at timestamptz NOT NULL,
    decision jsonb
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


def _ledger_net(conn, account_id: str) -> tuple[int, int] | None:
    row = conn.execute(
        """
        SELECT a.opening_balance AS opening,
               COALESCE(SUM(CASE WHEN e.direction = 'in' THEN e.amount WHEN e.direction = 'out' THEN -e.amount ELSE 0 END), 0) AS net
        FROM bank.accounts a
        LEFT JOIN bank.entries e ON e.account_id = a.id
        WHERE a.id = %s
        GROUP BY a.opening_balance
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["opening"]), int(row["net"])


def _set_opening(conn, account_id: str, target: int) -> None:
    """Đưa số dư về target bằng đầu kỳ. Không thêm dòng sổ, nên không phát sinh tiền vào."""
    found = _ledger_net(conn, account_id)
    if found is None:
        return
    _opening, net = found
    opening = target - net
    conn.execute(
        "UPDATE bank.accounts SET opening_balance = %s, balance = %s WHERE id = %s",
        (opening, opening + net, account_id),
    )


def _pension_story(conn) -> None:
    """Cảnh B: tất toán sổ tiết kiệm là cách duy nhất để đủ 120 triệu.

    Parquet quan sát được là nguồn lịch sử giao dịch. A0072556 có trong
    accounts.parquet (đầu kỳ 66.023.530, sổ trước 20/9 để số dư khoảng 92 triệu)
    và sổ A0077690 mở từ 2018 với đúng 48.091.787, không một dòng sổ.
    load() ghi đè hai số đó cho cảnh demo: Postgres sau load() là số app và
    engine đọc khi chuyển tiền. Không ghi dòng tiền vào trong cửa sổ 72 giờ.
    """
    _set_opening(conn, HUYNH_ACCOUNT, PENSION_BALANCE)
    _set_opening(conn, HUYNH_SAVINGS, SAVINGS_PRINCIPAL)
    conn.execute(
        """
        UPDATE bank.accounts
        SET account_type = 'savings', status = 'open'
        WHERE id = %s
        """,
        (HUYNH_SAVINGS,),
    )
    others = conn.execute(
        """
        SELECT id FROM bank.accounts
        WHERE customer_id = (SELECT customer_id FROM bank.accounts WHERE id = %s)
          AND account_type = 'personal'
          AND id <> %s
          AND balance >= %s
        """,
        (HUYNH_ACCOUNT, HUYNH_ACCOUNT, PENSION_BALANCE),
    ).fetchall()
    for row in others:
        _set_opening(conn, row["id"], OTHER_PERSONAL_CAP)


def load(conn) -> int:
    conn.execute(SCHEMA)
    conn.execute("ALTER TABLE bank.accounts ADD COLUMN IF NOT EXISTS opening_balance bigint NOT NULL DEFAULT 0")
    conn.execute(
        """
        TRUNCATE bank.entries, bank.transfers, bank.products, bank.guardian_requests,
                 bank.guardians, bank.beneficiaries, bank.accounts, bank.idempotency, bank.call_notices,
                 bank.experiment_sessions
        """
    )
    ids = related_ids()
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet")
    accounts["account_id"] = accounts["account_id"].astype(str)
    accounts = accounts.loc[accounts["account_id"].isin(ids)].copy()
    customers = pd.read_parquet(OBSERVED / "customers.parquet", columns=["customer_id", "full_name", "segment", "has_guardian"])
    customers["customer_id"] = customers["customer_id"].astype(str)
    segment = dict(zip(customers["customer_id"], customers["segment"].astype(str)))
    guardian = dict(zip(customers["customer_id"], customers["has_guardian"].fillna(False).astype(bool)))
    legal_name = dict(zip(customers["customer_id"], customers["full_name"].astype(str)))
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
        opening = _money(record.opening_balance)
        balance = opening + _money(inflow.get(record.account_id, 0)) - _money(outflow.get(record.account_id, 0))
        rows.append(
            (
                record.account_id,
                who or None,
                legal_name.get(who) or str(record.holder_name_ascii or record.account_id),
                segment.get(who, "thường"),
                balance,
                opening,
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
            (id, customer_id, holder_name, segment, balance, opening_balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows,
        )
    payees = pd.read_parquet(OBSERVED / "payees.parquet")
    payees["from_account"] = payees["from_account"].astype(str)
    payees["to_account"] = payees["to_account"].astype(str)
    payees = payees.loc[payees["from_account"].isin(ids) & (pd.to_datetime(payees["added_at"]) < CUT)]
    names = {row[0]: row[2] for row in rows}
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
    persona_ids = {item for person in payload["personas"] for item in person["account_ids"]}
    visible = history.loc[history["from_account"].isin(persona_ids) | history["to_account"].isin(persona_ids)]
    entries = []
    for record in visible.itertuples(index=False):
        purpose = None if pd.isna(record.purpose_text) else str(record.purpose_text)
        stamp = pd.Timestamp(record.ts).to_pydatetime()
        amount = _money(record.amount)
        kind = str(record.tx_type)
        tx_id = str(record.tx_id)
        sender = str(record.from_account)
        receiver = str(record.to_account) if pd.notna(record.to_account) else ""
        if sender in persona_ids:
            entries.append((sender, tx_id, stamp, "out", amount, kind, purpose, "done"))
        if receiver in persona_ids and receiver != sender:
            entries.append((receiver, tx_id, stamp, "in", amount, kind, purpose, "done"))
    if entries:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO bank.entries (account_id, tx_id, ts, direction, amount, tx_type, purpose_text, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                entries,
            )
    _pension_story(conn)
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
