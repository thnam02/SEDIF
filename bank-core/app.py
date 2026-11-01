"""Bank-core. Đóng dấu đồng hồ demo, gọi engine bằng API key, giữ tiền mức 2 và 3."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

import pandas as pd
import psycopg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from clock import ICT, DemoClock
from engine.common.hashing import account_hash, require_key
from engine.decide import REASONS
from engine.policy import actions_for
from load import CUT, OBSERVED, PERSONAS, SCHEMA, load
from notice import notice_allowed, notice_limits

log = logging.getLogger("bank")
DECIDE_TIMEOUT_S = 0.3
NEW_PAYEE_BANNER = 50_000_000
PURPOSE_AMOUNT_VND = int(os.environ.get("PURPOSE_AMOUNT_VND", "10000000"))
# Khớp funds_fresh_hours. Kênh lấy từ sổ: app, branch, hoặc unknown khi sổ không ghi.
FUNDS_HOURS = 72
BRANCH_CLOSURE_ID = "P-demo-branch-closure"
FUNDS_KINDS = (
    "term_deposit_closure",
    "savings_liquidation",
    "fast_loan_disbursement",
    "quick_loan_disbursement",
    "limit_increase",
    "limit_raise",
    "card_cash_advance",
    "credit_card_cash_advance",
)
# Danh sách demo, gắn nhãn. Không phải hồ sơ đường dây thật.
DEMO_MULE_ACCOUNTS = frozenset({"ZMULEDEMO0000001"})
RESET_AT = datetime.fromisoformat("2026-09-20T09:00:00+07:00")

CLOCK = DemoClock(speed=float(os.environ.get("DEMO_CLOCK_SPEED", "1")))
# Người nhận chỉ có trong các cảnh demo. Không phải khách thật.
DEMO_PAYEES = {
    "A0119434": "Công ty TNHH Thành Công",
    "ZCAP400000000001": "Đặng Quốc Bảo",
    "ZGUIDE4000000001": "Ngô Thanh Hà",
    "ZMULEDEMO0000001": "Trịnh Văn Khôi",
    "ZMED230000000001": "Lý Minh Châu",
    "ZMED220000000001": "Lý Hồng Nhung",
    "ZMED000000000001": "Lý Hồng Nhung",
    "ZLANB40000000001": "Phạm Thu Ngân",
    "ZLANB40000000002": "Mai Phương Thảo",
    "ZLAN120000000001": "Phạm Thu Ngân",
    "ZANH000000000099": "Võ Anh Thư",
    "ZVAY000000000001": "Bùi Hải Yến",
}
# Cùng bảng với bank-app/src/format.ts. Hậu tố giữ nguyên để fixture engine khớp. Thiếu thì đệm trái thành 10 số.
DEMO_NUMBERS = {
    "A0119434": "0901119434",
    "ZCAP400000000001": "0902401001",
    "ZGUIDE4000000001": "0902402001",
    "ZMULEDEMO0000001": "0902003001",
    "ZMED230000000001": "0902230230",
    "ZMED220000000001": "0902220220",
    "ZMED000000000001": "0902000001",
    "ZLANB40000000001": "0902101002",
    "ZLANB40000000002": "0902102002",
    "ZLAN120000000001": "0902120120",
    "ZANH000000000099": "0902000099",
    "ZVAY000000000001": "0902004001",
}
SKIP: set[str] = set()
SESSIONS: set[str] = set()
FEED: list[tuple[datetime, dict]] = []
FEED_CURSOR = 0
RESET_ID = ""
LEDGER_MISMATCH = False
_REPLAY_STARTED = False


def database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL thiếu, không khởi động")
    return raw


def connect():
    return psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)


class EngineUnavailable(Exception):
    pass


def reset_id() -> str:
    return RESET_ID


def remember_reset(conn, value: str) -> None:
    global RESET_ID
    RESET_ID = value
    conn.execute(
        """
        INSERT INTO bank.demo_state (id, reset_id) VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET reset_id = EXCLUDED.reset_id
        """,
        (value,),
    )


def load_reset(conn) -> None:
    global RESET_ID
    conn.execute(SCHEMA)
    row = conn.execute("SELECT reset_id FROM bank.demo_state WHERE id = 1").fetchone()
    RESET_ID = str(row["reset_id"]) if row else ""


def engine_reset_id() -> str | None:
    url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8091").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    return str(payload.get("reset_id", ""))


def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
    global LEDGER_MISMATCH
    url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8091").rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(body, default=str).encode(),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": os.environ.get("FIDES_API_KEY", ""),
            "X-Reset-Id": RESET_ID,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        if exc.code == 409 and "ledger_mismatch" in detail:
            LEDGER_MISMATCH = True
            log.warning("ledger_mismatch")
            raise HTTPException(409, "ledger_mismatch") from exc
        if exc.code >= 500:
            raise EngineUnavailable(detail) from exc
        raise HTTPException(exc.code, detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineUnavailable(str(exc)) from exc


def _us(stamp: datetime) -> int:
    return int(stamp.timestamp() * 1_000_000)


_ACCOUNT_KEYS = {"account", "account_id", "from_account", "to_account", "payee", "sender", "counterparty"}


def _is_hmac(text: str) -> bool:
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def hash_accounts(value):
    """Băm mã tài khoản trước khi ghi. Hash 64 hex giữ nguyên."""
    if isinstance(value, dict):
        hashed = {}
        for key, item in value.items():
            if key in _ACCOUNT_KEYS and isinstance(item, str) and not _is_hmac(item):
                hashed[key] = account_hash(item)
            else:
                hashed[key] = hash_accounts(item)
        return hashed
    if isinstance(value, list):
        return [hash_accounts(item) for item in value]
    return value


def _plain(value):
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def _ensure_experiment_column(conn) -> None:
    conn.execute("ALTER TABLE bank.experiment_sessions ADD COLUMN IF NOT EXISTS decision jsonb")


def touch_session(account: str, device: dict | None, now: datetime) -> str:
    session = f"demo-{account}"
    hashed = account_hash(account)
    try:
        if account not in SESSIONS:
            mode = (device or {}).get("acct_input") or "typed"
            with connect() as conn:
                customer = conn.execute("SELECT customer_id FROM bank.accounts WHERE id = %s", (account,)).fetchone()
            engine_post(
                "/events",
                {
                    "type": "session",
                    "session": session,
                    "account": hashed,
                    "start": _us(now),
                    "end": _us(datetime.fromisoformat("2026-09-29T00:00:00+07:00")),
                    "customer": (customer or {}).get("customer_id") or "",
                    "first_input": mode,
                },
                timeout=DECIDE_TIMEOUT_S,
            )
            SESSIONS.add(account)
        if device:
            engine_post(
                "/events",
                {
                    "type": "device_signal",
                    "ts": _us(now),
                    "session": session,
                    "in_call": "1" if device.get("in_call") else "0",
                    "ended": "1" if device.get("call_ended_in_session") else "0",
                },
                timeout=DECIDE_TIMEOUT_S,
            )
            if device.get("acct_input"):
                engine_post(
                    "/events",
                    {"type": "session_event", "ts": _us(now), "session": session, "event_type": f"acct_input_{device['acct_input']}"},
                    timeout=DECIDE_TIMEOUT_S,
                )
    except EngineUnavailable:
        log.warning("engine_unavailable")
    return session


def post_ledger(event: dict) -> None:
    try:
        engine_post("/events", event)
    except EngineUnavailable:
        log.warning("engine_unavailable")


def _payee_is_new(conn, sender: str, payee: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM bank.transfers t
        JOIN bank.accounts sender ON sender.id = t.from_account
        WHERE sender.customer_id = (SELECT customer_id FROM bank.accounts WHERE id = %s)
          AND t.to_account = %s
          AND t.status <> 'cancelled'
        LIMIT 1
        """,
        (sender, payee),
    ).fetchone()
    return row is None


_ACTION_LEVEL = {0: "none", 1: "banner", 2: "medium", 3: "high"}


def numeric_level(level) -> int:
    """/decide trả 0–3. /respond cũ vẫn trả chữ; cửa ngân hàng đổi về số trước khi trả khách."""
    if isinstance(level, bool):
        return 0
    if isinstance(level, int):
        return level
    return {"none": 0, "info": 0, "banner": 1, "medium": 2, "high": 3}.get(str(level), 0)


def _fallback(conn, sender: str, payee: str, amount: int) -> dict:
    log.warning("engine_unavailable")
    level = 1 if amount >= NEW_PAYEE_BANNER and _payee_is_new(conn, sender, payee) else 0
    reasons = [{"code": "new_payee", "text": REASONS[3][2]}] if level == 1 else []
    return {
        "level": level,
        "funds_status": "released",
        "hold_until": None,
        "countdown_until": None,
        "actions": actions_for(_ACTION_LEVEL[level], "thường", False),
        "reasons": reasons,
        "signals_present": ["new_payee"] if level == 1 else [],
    }


def status_of(level: int, funds: str, hung_up: bool) -> str:
    if funds == "cancelled":
        return "cancelled"
    if funds == "released":
        return "done" if level <= 1 else "released"
    if level >= 3 and not hung_up:
        return "blocked_pending_hangup"
    if level == 2:
        return "needs_verification"
    return "held"


def credit(conn, account: str, amount: int) -> None:
    conn.execute("UPDATE bank.accounts SET balance = balance + %s WHERE id = %s", (amount, account))


ADD_METHODS = frozenset({"typed", "paste", "qr", "contact"})


def add_method_for(task_type: str | None, entry_detail: str | None, explicit: str | None) -> str | None:
    """Cách nhập mà model-v2.1 đã học. Ảnh không có cột riêng, nên dùng paste."""
    if explicit in ADD_METHODS:
        return explicit
    if entry_detail in {"paste", "image_capture", "image_upload"}:
        return "paste"
    if task_type == "qr":
        return "qr"
    if task_type == "saved_payee":
        return "contact"
    if task_type == "new_pasted":
        return "paste"
    if task_type == "new_typed":
        return "typed"
    return None


def spent_today(conn, account: str, now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spent = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS n FROM bank.transfers
        WHERE from_account = %s AND created_at >= %s
          AND status NOT IN ('cancelled', 'awaiting_confirmation')
        """,
        (account, start),
    ).fetchone()
    return int(spent["n"])


def load_account(conn, account: str):
    row = conn.execute(
        "SELECT balance, segment, has_guardian, limit_per_txn, limit_per_day FROM bank.accounts WHERE id = %s",
        (account,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "không có tài khoản")
    return row


def reject_if_cannot_pay(row, amount: int, spent: int) -> None:
    if int(row["balance"]) < amount:
        raise HTTPException(400, "không đủ số dư")
    if amount > int(row["limit_per_txn"]):
        raise HTTPException(400, "vượt hạn mức giao dịch")
    if spent + amount > int(row["limit_per_day"]):
        raise HTTPException(400, "vượt hạn mức ngày")


def debit(conn, account: str, amount: int, now: datetime) -> None:
    row = load_account(conn, account)
    reject_if_cannot_pay(row, amount, spent_today(conn, account, now))
    conn.execute("UPDATE bank.accounts SET balance = balance - %s WHERE id = %s", (amount, account))


def entry(conn, account: str, tx_id: str, ts, direction: str, amount: int, tx_type: str, purpose: str, status: str, counterparty: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO bank.entries (account_id, tx_id, ts, direction, amount, tx_type, purpose_text, status, counterparty_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (account, tx_id, ts, direction, amount, tx_type, purpose, status, counterparty or None),
    )


def release_payee(conn, transfer: dict, now: datetime) -> None:
    if transfer["posted"]:
        return
    owned = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (transfer["to_account"],)).fetchone()
    if owned:
        credit(conn, transfer["to_account"], int(transfer["amount"]))
        entry(conn, transfer["to_account"], transfer["id"], now, "in", int(transfer["amount"]), "transfer", transfer["purpose_text"], "done", transfer["from_account"])
    balance = conn.execute("SELECT balance FROM bank.accounts WHERE id = %s", (transfer["from_account"],)).fetchone()
    session = f"demo-{transfer['from_account']}"
    post_ledger(
        {
            "event_id": f"txn:{transfer['id']}",
            "type": "txn",
            "ts": now.isoformat(),
            "account_hash": account_hash(transfer["from_account"]),
            "counterparty_hash": account_hash(transfer["to_account"]),
            "tx_type": "transfer",
            "amount": int(transfer["amount"]),
            "balance": int(balance["balance"]) if balance else 0,
            "session_id": session,
        }
    )
    conn.execute("UPDATE bank.transfers SET posted = true WHERE id = %s", (transfer["id"],))


def sync_entry(conn, transfer_id: str, account: str, funds: str, status: str) -> None:
    shown = "done" if funds == "released" else "cancelled" if funds == "cancelled" else status
    conn.execute(
        "UPDATE bank.entries SET status = %s WHERE tx_id = %s AND account_id = %s",
        (shown, transfer_id, account),
    )


def apply_funds(conn, transfer: dict, previous: str, funds: str, now: datetime) -> None:
    if funds == "released" and previous != "released":
        release_payee(conn, transfer, now)
    if funds == "cancelled" and previous != "cancelled":
        credit(conn, transfer["from_account"], int(transfer["amount"]))
        entry(conn, transfer["from_account"], transfer["id"] + ":refund", now, "in", int(transfer["amount"]), "transfer", "hoàn", "cancelled")


class CallIn(BaseModel):
    state: Literal["active", "ended_in_session", "none", "unknown"]
    observability: Literal["supported_observed", "supported_not_observed", "unavailable"]
    transport: Literal["cellular", "voip", "unknown"]


class BehaviourIn(BaseModel):
    median_gap_ms: float
    p90_gap_ms: float
    burst_count: int
    pause_to_action_ratio: float
    review_dwell_ms: int
    edit_count: int
    backtrack_count: int
    screen_revisit_count: int
    background_count: int
    background_total_ms: int
    session_duration_ms: int


class ExperimentIn(BaseModel):
    participant_id: str
    condition: str
    session_index: int


class TransferIn(BaseModel):
    from_account: str
    to_account: str
    amount: int
    purpose_text: str | None = None
    device: dict | None = None
    task_type: Literal["saved_payee", "new_typed", "new_pasted", "qr"] | None = None
    add_method: Literal["typed", "paste", "qr", "contact"] | None = None
    entry_detail: Literal["paste", "image_capture", "image_upload"] | None = None
    call: CallIn | None = None
    behaviour_features: BehaviourIn | None = None
    transfer_purpose: Literal[
        "authority_request",
        "fee_to_receive",
        "investment",
        "refund_request",
        "purchase",
        "family_friends",
        "debt_salary",
        "other",
        "not_asked",
    ] | None = None
    experiment: ExperimentIn | None = None
    recent_funds_events: list[dict] | None = None


class ExperimentSessionIn(BaseModel):
    participant_id: str
    condition: str
    session_index: int
    task_type: Literal["saved_payee", "new_typed", "new_pasted", "qr"]
    call: CallIn
    behaviour_features: BehaviourIn
    raw_events: list[dict] | None = None


class Level1Action(BaseModel):
    response_ms: int = 0


class RespondIn(BaseModel):
    who_guides: str | None = None
    guardian_confirm: bool | None = None
    hung_up: bool | None = None
    proceed: bool | None = None
    cancel: bool | None = None


class ProductIn(BaseModel):
    account_id: str
    amount: int
    device: dict | None = None


class LimitIn(BaseModel):
    account_id: str
    limit_per_txn: int
    limit_per_day: int
    device: dict | None = None


class GuardianIn(BaseModel):
    account_id: str
    guardian_phone_hash: str | None = None
    phone: str | None = None
    relation: str
    consent: bool = False


class ChoiceIn(BaseModel):
    choice: Literal["pause", "cancel", "continue"]
    response_ms: int


class PersonaIn(BaseModel):
    customer_id: str


app = FastAPI(title="Bank core")


def _persona_accounts(customer_id: str) -> set[str]:
    payload = json.loads(PERSONAS.read_text(encoding="utf-8"))
    for person in payload["personas"]:
        if person["customer_id"] == customer_id:
            return set(person["account_ids"])
    return set()


def _build_feed() -> None:
    global FEED
    start = pd.Timestamp(CUT)
    end = pd.Timestamp("2026-09-29T00:00:00+07:00")
    tx = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type", "from_balance_before"])
    tx["ts"] = pd.to_datetime(tx["ts"])
    tx = tx.loc[(tx["ts"] >= start) & (tx["ts"] < end)]
    rows = []
    for record in tx.itertuples(index=False):
        stamp = pd.Timestamp(record.ts).to_pydatetime()
        sender = str(record.from_account)
        rows.append(
            (
                stamp,
                sender,
                {
                    "event_id": f"txn:{record.tx_id}",
                    "type": "txn",
                    "ts": stamp.isoformat(),
                    "account_hash": account_hash(sender),
                    "counterparty_hash": account_hash(str(record.to_account)) if pd.notna(record.to_account) else None,
                    "tx_type": str(record.tx_type),
                    "amount": int(record.amount),
                    "balance": int(record.from_balance_before or 0),
                },
            )
        )
    rows.sort(key=lambda item: item[0])
    FEED = rows


def _play() -> None:
    global FEED_CURSOR
    while True:
        if CLOCK.paused or not CLOCK.feed or FEED_CURSOR >= len(FEED):
            threading.Event().wait(0.2)
            continue
        stamp, sender, event = FEED[FEED_CURSOR]
        if CLOCK.now() < stamp:
            threading.Event().wait(0.05)
            continue
        FEED_CURSOR += 1
        if sender in SKIP:
            continue
        try:
            post_ledger(event)
        except Exception:
            continue


def _fund_channel(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"app", "branch"}:
        return text
    return "unknown"


def ensure_fund_channel(conn) -> None:
    conn.execute("ALTER TABLE bank.products ADD COLUMN IF NOT EXISTS channel text")
    conn.execute("ALTER TABLE bank.entries ADD COLUMN IF NOT EXISTS channel text")


BANK_PREFIX = "1903"


def display_digits(account_id: str) -> str:
    """Số hiển thị 10 chữ số. Không đệm bằng số 0: giữ hậu tố và gắn tiền tố ngân hàng.

    HMAC băm cả mã nội bộ (account_hash), không băm số hiển thị và không cắt hậu tố.
    """
    mapped = DEMO_NUMBERS.get(account_id)
    raw = mapped if mapped is not None else "".join(char for char in account_id if char.isdigit())
    if not raw:
        return ""
    if mapped or len(raw) >= 10:
        return raw[-10:]
    return f"{BANK_PREFIX}{raw[-6:].zfill(6)}"


def seed_demo_payees(conn) -> None:
    """Tên cho số tài khoản chỉ xuất hiện trong demo. Không đè khách có trong sổ mô phỏng."""
    for account_id, name in DEMO_PAYEES.items():
        shown = display_digits(account_id)
        if len(shown) != 10 or not shown.isdigit():
            raise RuntimeError(f"số demo không đủ 10 chữ số: {account_id}")
        conn.execute(
            """
            INSERT INTO bank.accounts
            (id, customer_id, holder_name, segment, balance, status, has_guardian, account_type, bank_id, limit_per_txn, limit_per_day)
            VALUES (%s, NULL, %s, 'thường', 0, 'open', false, 'personal', 'B01', 0, 0)
            ON CONFLICT (id) DO UPDATE SET holder_name = EXCLUDED.holder_name
            WHERE bank.accounts.customer_id IS NULL
            """,
            (account_id, name),
        )


def _varied_stamp(key: str, funds: bool) -> datetime:
    """Giờ khác nhau trên các ngày trước 20/09. Khoản tiền vào thì trước cửa sổ 72 giờ."""
    import hashlib

    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    day = 15 + (digest % 2) if funds else 17 + (digest % 3)
    return datetime(2026, 9, day, 7 + (digest % 12), (digest // 7) % 60, (digest // 13) % 60, tzinfo=ICT)


def spread_frozen_stamps(conn) -> None:
    """Dòng demo từng đóng cùng một mốc được trải ra các ngày trước, để sổ không còn một giờ."""
    epoch = datetime(2026, 9, 20, tzinfo=ICT)
    frozen = (epoch, RESET_AT)
    funds = set(FUNDS_KINDS)
    for table, column, key in (
        ("bank.entries", "ts", "tx_id"),
        ("bank.transfers", "created_at", "id"),
        ("bank.products", "ts", "id"),
    ):
        rows = conn.execute(
            f"SELECT {key} AS key, {column} AS ts FROM {table} WHERE {column} = %s OR {column} = %s",
            frozen,
        ).fetchall()
        for row in rows:
            if table == "bank.products" and row["key"] == BRANCH_CLOSURE_ID:
                continue
            kind = ""
            if table == "bank.products":
                kind = conn.execute("SELECT kind FROM bank.products WHERE id = %s", (row["key"],)).fetchone()["kind"]
            elif table == "bank.entries":
                kind = conn.execute("SELECT tx_type FROM bank.entries WHERE tx_id = %s LIMIT 1", (row["key"],)).fetchone()["tx_type"]
            stamp = _varied_stamp(str(row["key"]), kind in funds)
            conn.execute(f"UPDATE {table} SET {column} = %s WHERE {key} = %s AND ({column} = %s OR {column} = %s)", (stamp, row["key"], epoch, RESET_AT))


def backfill_counterparties(conn) -> None:
    """Gắn số tài khoản bên kia vào sổ, để lịch sử hiện tên thay vì mã giao dịch."""
    conn.execute("ALTER TABLE bank.entries ADD COLUMN IF NOT EXISTS counterparty_id text")
    conn.execute("CREATE TABLE IF NOT EXISTS bank.meta (key text PRIMARY KEY, value text)")
    conn.execute(
        """
        UPDATE bank.entries e
        SET counterparty_id = CASE
            WHEN e.direction = 'out' THEN NULLIF(t.to_account, '')
            ELSE NULLIF(t.from_account, '')
        END
        FROM bank.transfers t
        WHERE t.id = e.tx_id AND (e.counterparty_id IS NULL OR e.counterparty_id = '')
        """
    )
    from load import OBSERVED

    path = OBSERVED / "transactions.parquet"
    if not path.exists():
        return
    wanted = {
        row["tx_id"]
        for row in conn.execute(
            """
            SELECT DISTINCT tx_id FROM bank.entries
            WHERE (counterparty_id IS NULL OR counterparty_id = '')
              AND tx_type IN ('transfer', 'own_transfer', 'own_transfer_drain', 'passthrough_in', 'passthrough_out')
            """
        ).fetchall()
    }
    if not wanted:
        conn.execute("INSERT INTO bank.meta (key, value) VALUES ('parties_v1', '1') ON CONFLICT (key) DO NOTHING")
        return
    frame = pd.read_parquet(path, columns=["tx_id", "from_account", "to_account"])
    frame["tx_id"] = frame["tx_id"].astype(str)
    frame = frame.loc[frame["tx_id"].isin(wanted)]
    pairs = [
        (str(row.to_account), str(row.from_account), str(row.tx_id))
        for row in frame.itertuples(index=False)
        if str(row.to_account) not in {"", "nan", "None"}
    ]
    if pairs:
        conn.execute("CREATE TEMP TABLE party_fill (to_account text, from_account text, tx_id text) ON COMMIT DROP")
        with conn.cursor() as cur:
            with cur.copy("COPY party_fill (to_account, from_account, tx_id) FROM STDIN") as stream:
                for row in pairs:
                    stream.write_row(row)
        conn.execute(
            """
            UPDATE bank.entries e
            SET counterparty_id = CASE WHEN e.direction = 'out' THEN p.to_account ELSE p.from_account END
            FROM party_fill p
            WHERE e.tx_id = p.tx_id AND (e.counterparty_id IS NULL OR e.counterparty_id = '')
            """
        )
    conn.execute("INSERT INTO bank.meta (key, value) VALUES ('parties_v1', '1') ON CONFLICT (key) DO NOTHING")


def ensure_scene_capacity(conn) -> None:
    """Cảnh B chuyển 120 triệu. Sổ parquet để hạn mức 100 triệu."""
    conn.execute(
        """
        UPDATE bank.accounts
        SET limit_per_txn = GREATEST(limit_per_txn, 500000000),
            limit_per_day = GREATEST(limit_per_day, 5000000000)
        WHERE id = 'A0072556'
        """
    )


def seed_branch_closure(conn) -> None:
    """Một tất toán tại quầy, gắn nhãn demo. Không nằm trên tài khoản các cảnh Maestro."""
    ensure_fund_channel(conn)
    conn.execute(
        """
        INSERT INTO bank.products (id, account_id, amount, kind, ts, channel)
        VALUES (%s, 'A0119955', 1000000, 'savings_liquidation', %s, 'branch')
        ON CONFLICT (id) DO NOTHING
        """,
        (BRANCH_CLOSURE_ID, datetime.fromisoformat("2026-09-20T07:12:00+07:00")),
    )
    conn.execute(
        "UPDATE bank.products SET ts = %s WHERE id = %s",
        (datetime.fromisoformat("2026-09-20T07:12:00+07:00"), BRANCH_CLOSURE_ID),
    )


def boot() -> None:
    """Không nạp lại sổ. BANK_RESEED=1 mới nạp, và sinh reset_id mới chỉ ở phía ngân hàng."""
    global LEDGER_MISMATCH, _REPLAY_STARTED
    require_key()
    with connect() as conn:
        if os.environ.get("BANK_RESEED", "0") == "1":
            SESSIONS.clear()
            load(conn)
            remember_reset(conn, str(uuid.uuid4()))
            LEDGER_MISMATCH = True
        else:
            load_reset(conn)
        seed_branch_closure(conn)
        seed_demo_payees(conn)
        ensure_scene_capacity(conn)
        spread_frozen_stamps(conn)
        backfill_counterparties(conn)
        conn.commit()
    CLOCK.reanchor()
    if os.environ.get("DEMO_REPLAY", "1") == "1" and not _REPLAY_STARTED:
        threading.Thread(target=_build_feed, daemon=True).start()
        threading.Thread(target=_play, daemon=True).start()
        _REPLAY_STARTED = True


@app.on_event("startup")
def startup() -> None:
    boot()


@app.get("/health")
def health() -> dict:
    global LEDGER_MISMATCH
    remote = engine_reset_id()
    if remote is not None and remote != RESET_ID:
        LEDGER_MISMATCH = True
    return {"status": "ok", "service": "bank-core", "reset_id": RESET_ID, "ledger_mismatch": LEDGER_MISMATCH}


@app.get("/clock")
def clock() -> dict:
    return CLOCK.state()


@app.post("/clock/pause")
def pause() -> dict:
    CLOCK.pause()
    return CLOCK.state()


@app.post("/clock/resume")
def resume() -> dict:
    CLOCK.resume()
    return CLOCK.state()


class ClockSeek(BaseModel):
    ts: str


class ClockAdvance(BaseModel):
    seconds: float


@app.post("/clock/seek")
def seek(body: ClockSeek) -> dict:
    CLOCK.seek(datetime.fromisoformat(body.ts))
    return CLOCK.state()


@app.post("/clock/advance")
def advance(body: ClockAdvance) -> dict:
    CLOCK.advance(body.seconds)
    return CLOCK.state()


class ResetIn(BaseModel):
    reset_id: str


@app.post("/demo/reset")
def demo_reset(body: ResetIn) -> dict:
    global SKIP, FEED_CURSOR, LEDGER_MISMATCH
    CLOCK.pause()
    SESSIONS.clear()
    SKIP = set()
    FEED_CURSOR = 0
    with connect() as conn:
        load(conn)
        ensure_scene_capacity(conn)
        remember_reset(conn, body.reset_id)
        conn.commit()
    LEDGER_MISMATCH = False
    CLOCK.reanchor()
    return {"status": "ok", "reset_id": RESET_ID, **CLOCK.state()}


class NoticeIn(BaseModel):
    demo: bool = False


@app.post("/accounts/{account_id}/call-notice")
def call_notice(account_id: str, body: NoticeIn) -> dict:
    limit, days = notice_limits()
    now = CLOCK.now()
    cutoff = now - timedelta(days=days)
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        used = int(
            conn.execute(
                "SELECT count(*) AS n FROM bank.call_notices WHERE account_id = %s AND shown_at >= %s",
                (account_id, cutoff),
            ).fetchone()["n"]
        )
        show = notice_allowed(used, limit, body.demo)
        if show and not body.demo:
            conn.execute("INSERT INTO bank.call_notices (account_id, shown_at) VALUES (%s, %s)", (account_id, now))
            conn.commit()
    return {"show": show, "used": used, "max": limit}


@app.post("/demo/persona")
def persona(body: PersonaIn) -> dict:
    global SKIP
    SKIP = _persona_accounts(body.customer_id)
    return {"customer_id": body.customer_id, "skipped_accounts": sorted(SKIP)}


@app.get("/accounts/{account_id}")
def get_account(account_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "không có tài khoản")
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "holder_name": row["holder_name"],
        "segment": row["segment"] if row["segment"] in {"thường", "cao tuổi", "doanh nghiệp nhỏ"} else "thường",
        "balance": int(row["balance"]),
        "status": row["status"] if row["status"] in {"open", "frozen", "closed"} else "open",
        "has_guardian": bool(row["has_guardian"]),
        "account_type": row["account_type"] if row["account_type"] in {"personal", "savings", "business", "securities", "ewallet"} else "personal",
        "limit_per_txn": int(row["limit_per_txn"]),
        "limit_per_day": int(row["limit_per_day"]),
    }


@app.get("/accounts/{account_id}/history")
def history(account_id: str, before: str | None = None) -> dict:
    cutoff = datetime.fromisoformat(before) if before else CLOCK.now()
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        rows = conn.execute(
            """
            SELECT e.tx_id, e.ts, e.direction, e.amount, e.tx_type, e.purpose_text, e.status,
                   COALESCE(NULLIF(e.counterparty_id, ''), x.other_id, pair.other_id) AS counterparty_id,
                   COALESCE(NULLIF(cp.holder_name, ''), NULLIF(ben.holder_name, '')) AS counterparty_name
            FROM bank.entries e
            LEFT JOIN bank.transfers t ON t.id = e.tx_id
            LEFT JOIN LATERAL (
                SELECT CASE
                    WHEN e.direction = 'out' THEN NULLIF(t.to_account, '')
                    ELSE NULLIF(t.from_account, '')
                END AS other_id
            ) x ON true
            LEFT JOIN LATERAL (
                SELECT e2.account_id AS other_id
                FROM bank.entries e2
                WHERE e2.tx_id = e.tx_id AND e2.account_id <> e.account_id
                ORDER BY e2.id
                LIMIT 1
            ) pair ON true
            LEFT JOIN bank.accounts cp ON cp.id = COALESCE(NULLIF(e.counterparty_id, ''), x.other_id, pair.other_id)
            LEFT JOIN bank.beneficiaries ben
                ON ben.owner_account = e.account_id
               AND ben.account_id = COALESCE(NULLIF(e.counterparty_id, ''), x.other_id, pair.other_id)
            WHERE e.account_id = %s AND e.ts <= %s
            ORDER BY e.ts, e.id
            """,
            (account_id, cutoff),
        ).fetchall()
    return {
        "account_id": account_id,
        "entries": [
            {
                "tx_id": row["tx_id"],
                "ts": row["ts"].isoformat(),
                "direction": row["direction"],
                "amount": int(row["amount"]),
                "tx_type": row["tx_type"],
                "purpose_text": row["purpose_text"],
                "status": row["status"],
                "counterparty_id": row["counterparty_id"],
                "counterparty_name": row["counterparty_name"],
            }
            for row in rows
        ],
    }


@app.get("/accounts/{account_id}/beneficiaries")
def list_beneficiaries(account_id: str) -> dict:
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        rows = conn.execute(
            """
            SELECT id, account_id, holder_name, saved_at, times_paid
            FROM bank.beneficiaries WHERE owner_account = %s ORDER BY times_paid DESC, holder_name
            """,
            (account_id,),
        ).fetchall()
    return {
        "account_id": account_id,
        "beneficiaries": [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "holder_name": row["holder_name"],
                "saved_at": row["saved_at"].isoformat(),
                "times_paid": int(row["times_paid"]),
            }
            for row in rows
        ],
    }


@app.get("/accounts/{account_id}/savings")
def list_savings(account_id: str) -> dict:
    with connect() as conn:
        owner = conn.execute("SELECT customer_id FROM bank.accounts WHERE id = %s", (account_id,)).fetchone()
        if owner is None:
            raise HTTPException(404, "không có tài khoản")
        rows = conn.execute(
            """
            SELECT id, holder_name, balance
            FROM bank.accounts
            WHERE customer_id = %s AND account_type = 'savings' AND status = 'open'
            ORDER BY id
            """,
            (owner["customer_id"],),
        ).fetchall()
    return {
        "account_id": account_id,
        "books": [{"id": row["id"], "holder_name": row["holder_name"], "balance": int(row["balance"])} for row in rows],
    }


@app.get("/beneficiaries/{beneficiary_id}")
def beneficiary(beneficiary_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.beneficiaries WHERE id = %s", (beneficiary_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "không có người thụ hưởng")
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "holder_name": row["holder_name"],
        "saved_at": row["saved_at"].isoformat(),
        "times_paid": int(row["times_paid"]),
    }


def _next_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def ledger_funds(conn, account: str, now: datetime) -> list[dict]:
    """Sổ của bank-core. App không biết các lệnh này."""
    start = now - timedelta(hours=FUNDS_HOURS)
    rows = conn.execute(
        """
        SELECT type, ts, channel FROM (
            SELECT kind AS type, ts, channel FROM bank.products
            WHERE account_id = %s AND kind = ANY(%s) AND ts >= %s AND ts <= %s
            UNION ALL
            SELECT tx_type AS type, ts, channel FROM bank.entries
            WHERE account_id = %s AND tx_type = ANY(%s) AND direction = 'in' AND ts >= %s AND ts <= %s
        ) events
        ORDER BY ts
        """,
        (account, list(FUNDS_KINDS), start, now, account, list(FUNDS_KINDS), start, now),
    ).fetchall()
    events: dict[tuple[str, str], dict] = {}
    for row in rows:
        stamp = row["ts"]
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ICT)
        at = stamp.isoformat()
        kind = str(row["type"])
        channel = _fund_channel(row["channel"])
        key = (kind, at)
        current = events.get(key)
        if current is None or (current["channel"] == "unknown" and channel != "unknown"):
            events[key] = {"type": kind, "at": at, "channel": channel}
    return list(events.values())


def verified_mule(account: str) -> list[dict]:
    if account not in DEMO_MULE_ACCOUNTS:
        return []
    return [
        {
            "signal_id": "recipient_bank_confirmed_mule",
            "status": "present",
            "source": "core_banking",
            "active": True,
        }
    ]


@app.post("/transfer")
def transfer(
    body: TransferIn,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    now = CLOCK.now()
    key = (idempotency_key or "").strip() or None
    if key:
        with connect() as conn:
            prior = conn.execute("SELECT response FROM bank.idempotency WHERE key = %s", (key,)).fetchone()
        if prior is not None:
            stored = prior["response"]
            return stored if isinstance(stored, dict) else json.loads(stored)
    session = touch_session(body.from_account, body.device, now)
    with connect() as conn:
        account = load_account(conn, body.from_account)
        spent = spent_today(conn, body.from_account, now)
        reject_if_cannot_pay(account, body.amount, spent)
        transfer_id = _next_id("X")
        decision_id = f"dec-{transfer_id}"
        method = add_method_for(body.task_type, body.entry_detail, body.add_method)
        if method:
            try:
                engine_post(
                    "/events",
                    {
                        "type": "payee_add",
                        "ts": _us(now),
                        "account": account_hash(body.from_account),
                        "payee": account_hash(body.to_account),
                        "method": method,
                    },
                    timeout=DECIDE_TIMEOUT_S,
                )
            except EngineUnavailable:
                log.warning("payee_add failed")
        request = {
            "decision_id": decision_id,
            "profile": "advanced",
            "sender_hash": account_hash(body.from_account),
            "payee_hash": account_hash(body.to_account),
            "amount": body.amount,
            "balance": int(account["balance"]),
            "ts": now.isoformat(),
            "session_id": session,
            "score_request": True,
            "limit_per_txn": int(account["limit_per_txn"]),
            "limit_per_day": int(account["limit_per_day"]),
            "spent_today": spent,
        }
        if method:
            request["add_method"] = method
        if body.device is not None:
            request["device"] = body.device
        for field in ("task_type", "call", "behaviour_features", "transfer_purpose", "experiment"):
            sent = _plain(getattr(body, field))
            if sent is not None:
                request[field] = sent
        request["recent_funds_events"] = ledger_funds(conn, body.from_account, now)
        request["guardian_consent"] = _guardian_consented(conn, body.from_account)
        mule = verified_mule(body.to_account)
        if mule:
            request["signals"] = mule
        try:
            decided = engine_post("/decide", request, timeout=DECIDE_TIMEOUT_S)
        except EngineUnavailable:
            decided = _fallback(conn, body.from_account, body.to_account, body.amount)
            decision_id = None
        level = numeric_level(decided["level"])
        funds = decided["funds_status"]
        experiment = body.experiment
        if experiment is not None:
            record = {
                "experiment": _plain(experiment),
                "level": decided.get("level"),
                "rules_fired": list(decided.get("rules_fired") or []),
                "evidence_card": decided.get("evidence_card") or {},
            }
            log.info("experiment decision %s", json.dumps(record, ensure_ascii=False))
            funds = "released"
            decided["hold_until"] = None
        waiting = level == 1 and experiment is None
        if waiting:
            funds = "held"
            status = "awaiting_confirmation"
        else:
            debit(conn, body.from_account, body.amount, now)
            status = "done" if experiment is not None else status_of(level, funds, False)
        conn.execute(
            """
            INSERT INTO bank.transfers
            (id, from_account, to_account, amount, purpose_text, decision_id, level, funds_status, status,
             hold_until, countdown_until, gate, hung_up, posted, actions, reasons, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,%s::jsonb,%s::jsonb,%s)
            """,
            (
                transfer_id,
                body.from_account,
                body.to_account,
                body.amount,
                body.purpose_text,
                decision_id,
                str(level),
                funds,
                status,
                decided.get("hold_until"),
                decided.get("countdown_until"),
                "none",
                json.dumps(decided.get("actions") or []),
                json.dumps(decided.get("reasons") or []),
                now,
            ),
        )
        if not waiting:
            entry(conn, body.from_account, transfer_id, now, "out", body.amount, "transfer", body.purpose_text or "", status, body.to_account)
            stored = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (transfer_id,)).fetchone()
            if funds == "released":
                release_payee(conn, stored, now)
        if experiment is not None:
            _save_experiment_decision(conn, body, decided, now)
        if level == 2 and experiment is None and _guardian_consented(conn, body.from_account):
            conn.execute(
                """
                INSERT INTO bank.guardian_requests (id, account_id, transfer_id, level, funds_status, status)
                VALUES (%s,%s,%s,%s,%s,'open')
                """,
                (f"G-{transfer_id}", body.from_account, transfer_id, str(level), funds),
            )
            log.info("guardian notify mock account=%s transfer=%s", body.from_account, transfer_id)
        conn.commit()
    decision = _decision_payload(level, decided)
    if waiting:
        decision["intervention"] = "reminder"
    result = {
        "transfer_id": transfer_id,
        "status": status,
        "funds_status": funds,
        "hold_until": decided.get("hold_until"),
        "decision": decision,
    }
    if key:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO bank.idempotency (key, transfer_id, response)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (key) DO NOTHING
                """,
                (key, transfer_id, json.dumps(result, ensure_ascii=False)),
            )
            conn.commit()
            prior = conn.execute("SELECT response FROM bank.idempotency WHERE key = %s", (key,)).fetchone()
        stored = prior["response"]
        return stored if isinstance(stored, dict) else json.loads(stored)
    return result


def log_choice(decision_id: str | None, choice: str, response_ms: int) -> None:
    if not decision_id:
        return
    try:
        engine_post(
            f"/decisions/{decision_id}/choice",
            {"choice": choice, "response_ms": max(0, int(response_ms))},
        )
    except EngineUnavailable as exc:
        log.warning("choice not logged: %s", exc)
    except HTTPException as exc:
        if exc.status_code != 409:
            log.warning("choice not logged: %s", exc.detail)


def _level1_decision(transfer: dict) -> dict:
    reasons = transfer["reasons"] if isinstance(transfer["reasons"], list) else json.loads(transfer["reasons"] or "[]")
    actions = transfer["actions"] if isinstance(transfer["actions"], list) else json.loads(transfer["actions"] or "[]")
    return {
        "level": 1,
        "actions": actions,
        "reasons": reasons,
        "signals_present": [],
        "rules_fired": [],
        "evidence_card": {"signals": [], "reasons_vi": [], "baseline_quality": "none", "episode_id": ""},
        "intervention": "reminder",
    }


def _settle_level1(conn, transfer: dict, now: datetime) -> None:
    debit(conn, transfer["from_account"], int(transfer["amount"]), now)
    entry(
        conn,
        transfer["from_account"],
        transfer["id"],
        now,
        "out",
        int(transfer["amount"]),
        "transfer",
        transfer["purpose_text"] or "",
        "done",
        transfer["to_account"],
    )
    conn.execute(
        "UPDATE bank.transfers SET funds_status = 'released', status = 'done' WHERE id = %s",
        (transfer["id"],),
    )
    stored = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (transfer["id"],)).fetchone()
    release_payee(conn, stored, now)


@app.post("/transfers/{transfer_id}/confirm")
def confirm_level1(transfer_id: str, body: Level1Action | None = None) -> dict:
    """Mức 1: trừ tiền và hoàn tất. Gọi lại trả cùng kết quả, không trừ lần hai."""
    now = CLOCK.now()
    response_ms = 0 if body is None else body.response_ms
    if response_ms < 0:
        raise HTTPException(400, "response_ms không hợp lệ")
    with connect() as conn:
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s FOR UPDATE", (transfer_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, "không có lệnh")
        if transfer["status"] == "done" and int(transfer["level"] or 0) == 1:
            conn.commit()
            return {
                "transfer_id": transfer_id,
                "status": "done",
                "funds_status": "released",
                "hold_until": None,
                "decision": _level1_decision(transfer),
            }
        if transfer["status"] != "awaiting_confirmation":
            raise HTTPException(409, "chỉ lệnh mức 1 đang chờ mới xác nhận được")
        _settle_level1(conn, transfer, now)
        conn.commit()
    log_choice(transfer["decision_id"], "continue", response_ms)
    return {
        "transfer_id": transfer_id,
        "status": "done",
        "funds_status": "released",
        "hold_until": None,
        "decision": _level1_decision(transfer),
    }


@app.post("/transfers/{transfer_id}/cancel")
def cancel_level1(transfer_id: str, body: Level1Action | None = None) -> dict:
    """Mức 1: bỏ lệnh. Chưa trừ tiền."""
    response_ms = 0 if body is None else body.response_ms
    if response_ms < 0:
        raise HTTPException(400, "response_ms không hợp lệ")
    with connect() as conn:
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s FOR UPDATE", (transfer_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, "không có lệnh")
        if transfer["status"] == "cancelled" and int(transfer["level"] or 0) == 1:
            conn.commit()
            return {"transfer_id": transfer_id, "status": "cancelled", "funds_status": "cancelled"}
        if transfer["status"] != "awaiting_confirmation":
            raise HTTPException(409, "chỉ lệnh mức 1 đang chờ mới huỷ được")
        conn.execute(
            "UPDATE bank.transfers SET funds_status = 'cancelled', status = 'cancelled' WHERE id = %s",
            (transfer_id,),
        )
        conn.commit()
    log_choice(transfer["decision_id"], "cancel", response_ms)
    return {"transfer_id": transfer_id, "status": "cancelled", "funds_status": "cancelled"}


def _decision_payload(level: int, decided: dict) -> dict:
    shown = level if level in {0, 1, 2, 3} else 0
    intervention = decided.get("intervention") or {0: "none", 1: "banner", 2: "three_button", 3: "security_workflow"}[shown]
    card = decided.get("evidence_card") or {"signals": [], "reasons_vi": [], "baseline_quality": "none", "episode_id": ""}
    return {
        "level": shown,
        "actions": decided.get("actions") or [],
        "reasons": decided.get("reasons") or [],
        "signals_present": list(decided.get("signals_present") or []),
        "rules_fired": list(decided.get("rules_fired") or []),
        "evidence_card": card,
        "intervention": intervention,
    }


def _save_experiment_decision(conn, body: TransferIn, decided: dict, now: datetime) -> None:
    experiment = body.experiment
    if experiment is None:
        return
    _ensure_experiment_column(conn)
    stored = {
        "level": decided.get("level"),
        "rules_fired": list(decided.get("rules_fired") or []),
        "evidence_card": decided.get("evidence_card") or {},
        "experiment": _plain(experiment),
    }
    payload = json.dumps(hash_accounts(stored), ensure_ascii=False)
    found = conn.execute(
        """
        UPDATE bank.experiment_sessions
        SET decision = %s::jsonb
        WHERE participant_id = %s AND condition = %s AND session_index = %s
        RETURNING id
        """,
        (payload, experiment.participant_id, experiment.condition, experiment.session_index),
    ).fetchone()
    if found is not None:
        return
    call = _plain(body.call) or {"state": "unknown", "observability": "unavailable", "transport": "unknown"}
    features = _plain(body.behaviour_features) or {}
    conn.execute(
        """
        INSERT INTO bank.experiment_sessions
        (participant_id, condition, session_index, task_type, call, behaviour_features, raw_events, created_at, decision)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, NULL, %s, %s::jsonb)
        """,
        (
            experiment.participant_id,
            experiment.condition,
            experiment.session_index,
            body.task_type or "new_typed",
            json.dumps(hash_accounts(call)),
            json.dumps(hash_accounts(features)),
            now,
            payload,
        ),
    )


@app.post("/experiment/sessions")
def store_experiment_session(body: ExperimentSessionIn) -> dict:
    now = CLOCK.now()
    raw = hash_accounts(body.raw_events) if body.raw_events is not None else None
    call = json.dumps(hash_accounts(body.call.model_dump()))
    features = json.dumps(hash_accounts(body.behaviour_features.model_dump()))
    with connect() as conn:
        conn.execute(SCHEMA)
        _ensure_experiment_column(conn)
        found = conn.execute(
            """
            SELECT id FROM bank.experiment_sessions
            WHERE participant_id = %s AND condition = %s AND session_index = %s
            """,
            (body.participant_id, body.condition, body.session_index),
        ).fetchone()
        if found is None:
            conn.execute(
                """
                INSERT INTO bank.experiment_sessions
                (participant_id, condition, session_index, task_type, call, behaviour_features, raw_events, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    body.participant_id,
                    body.condition,
                    body.session_index,
                    body.task_type,
                    call,
                    features,
                    None if raw is None else json.dumps(raw),
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE bank.experiment_sessions
                SET task_type = %s, call = %s::jsonb, behaviour_features = %s::jsonb, raw_events = %s::jsonb
                WHERE id = %s
                """,
                (body.task_type, call, features, None if raw is None else json.dumps(raw), found["id"]),
            )
        conn.commit()
    return {"stored": True}


@app.post("/transfer/{transfer_id}/respond")
def respond(transfer_id: str, body: RespondIn) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (transfer_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, "không có lệnh")
        payload = {key: value for key, value in body.model_dump().items() if value is not None}
        payload["decision_id"] = transfer["decision_id"]
        payload["ts"] = now.isoformat()
        answered = engine_post("/respond", payload)
        previous = transfer["funds_status"]
        funds = answered["funds_status"]
        level = numeric_level(answered["level"])
        hung = bool(transfer["hung_up"] or body.hung_up)
        status = status_of(level, funds, hung)
        apply_funds(conn, transfer, previous, funds, now)
        conn.execute(
            """
            UPDATE bank.transfers
            SET level=%s, funds_status=%s, status=%s, hold_until=%s, countdown_until=%s, gate=%s, hung_up=%s
            WHERE id=%s
            """,
            (str(level), funds, status, answered.get("hold_until"), answered.get("countdown_until"), answered.get("gate"), hung, transfer_id),
        )
        sync_entry(conn, transfer_id, transfer["from_account"], funds, status)
        conn.commit()
    return {
        "transfer_id": transfer_id,
        "status": status,
        "level": level,
        "funds_status": funds,
        "gate": answered.get("gate"),
        "hold_until": answered.get("hold_until"),
        "countdown_until": answered.get("countdown_until"),
    }


def _product(body: ProductIn, kind: str, tx_type: str) -> dict:
    """Một lệnh tất toán cho cùng tài khoản, loại và số tiền. Lần gọi sau trả về dòng đã có."""
    now = CLOCK.now()
    touch_session(body.account_id, body.device, now)
    fresh = False
    with connect() as conn:
        found = conn.execute(
            """
            SELECT tx_id FROM bank.entries
            WHERE account_id = %s AND tx_type = %s AND direction = 'in' AND amount = %s AND left(tx_id, 2) = 'P-'
            ORDER BY ts DESC
            LIMIT 1
            """,
            (body.account_id, tx_type, body.amount),
        ).fetchone()
        if found:
            product_id = found["tx_id"]
        else:
            fresh = True
            product_id = f"P-{uuid.uuid4().hex[:16]}"
            credit(conn, body.account_id, body.amount)
            entry(conn, body.account_id, product_id, now, "in", body.amount, tx_type, kind, "done")
            conn.execute(
                "INSERT INTO bank.products (id, account_id, amount, kind, ts, channel) VALUES (%s,%s,%s,%s,%s,'app')",
                (product_id, body.account_id, body.amount, kind, now),
            )
            conn.commit()
    if fresh:
        post_ledger(
            {
                "event_id": f"product:{product_id}",
                "type": "product",
                "ts": now.isoformat(),
                "account_hash": account_hash(body.account_id),
                "product_type": tx_type,
                "tx_type": tx_type,
                "amount": body.amount,
                "session_id": f"demo-{body.account_id}",
            }
        )
    return {"id": product_id, "status": "done"}


def _savings_book(conn, checking_id: str):
    """Sổ tiết kiệm đang mở của cùng khách. Không có sổ thì tất toán chỉ ghi có, như các bài test."""
    return conn.execute(
        """
        SELECT s.id, s.balance
        FROM bank.accounts s
        JOIN bank.accounts c ON c.customer_id = s.customer_id
        WHERE c.id = %s
          AND s.id <> c.id
          AND s.account_type = 'savings'
          AND s.status = 'open'
          AND s.balance > 0
        ORDER BY s.balance DESC
        LIMIT 1
        """,
        (checking_id,),
    ).fetchone()


@app.post("/savings/liquidate")
def liquidate(body: ProductIn) -> dict:
    """Tất toán một lần: trừ sổ, ghi có đúng một dòng vào tài khoản thanh toán, báo engine.

    Lần gọi sau cùng tài khoản, loại và số tiền trả về dòng đã có, không cộng thêm.
    """
    now = CLOCK.now()
    touch_session(body.account_id, body.device, now)
    fresh = False
    with connect() as conn:
        found = conn.execute(
            """
            SELECT tx_id FROM bank.entries
            WHERE account_id = %s AND tx_type = 'savings_liquidation' AND direction = 'in'
              AND amount = %s AND left(tx_id, 2) = 'P-'
            ORDER BY ts DESC
            LIMIT 1
            """,
            (body.account_id, body.amount),
        ).fetchone()
        if found:
            product_id = found["tx_id"]
        else:
            book = _savings_book(conn, body.account_id)
            if book is not None and int(book["balance"]) < body.amount:
                raise HTTPException(400, "không đủ số dư")
            fresh = True
            product_id = f"P-{uuid.uuid4().hex[:16]}"
            if book is not None:
                updated = conn.execute(
                    """
                    UPDATE bank.accounts
                    SET balance = balance - %s
                    WHERE id = %s AND status = 'open' AND balance >= %s
                    RETURNING balance
                    """,
                    (body.amount, book["id"], body.amount),
                ).fetchone()
                if updated is None:
                    raise HTTPException(400, "không đủ số dư")
                if int(updated["balance"]) == 0:
                    conn.execute("UPDATE bank.accounts SET status = 'closed' WHERE id = %s", (book["id"],))
                entry(conn, book["id"], product_id, now, "out", body.amount, "savings_liquidation", "savings_liquidation", "done")
            credit(conn, body.account_id, body.amount)
            entry(conn, body.account_id, product_id, now, "in", body.amount, "savings_liquidation", "savings_liquidation", "done")
            conn.execute(
                "INSERT INTO bank.products (id, account_id, amount, kind, ts, channel) VALUES (%s,%s,%s,'savings_liquidation',%s,'app')",
                (product_id, body.account_id, body.amount, now),
            )
            conn.commit()
    if fresh:
        post_ledger(
            {
                "event_id": f"product:{product_id}",
                "type": "product",
                "ts": now.isoformat(),
                "account_hash": account_hash(body.account_id),
                "product_type": "savings_liquidation",
                "tx_type": "savings_liquidation",
                "amount": body.amount,
                "session_id": f"demo-{body.account_id}",
            }
        )
    return {"id": product_id, "status": "done"}


@app.post("/loan")
def loan(body: ProductIn) -> dict:
    return _product(body, "quick_loan_disbursement", "quick_loan_disbursement")


@app.post("/card/cash-advance")
def cash_advance(body: ProductIn) -> dict:
    return _product(body, "credit_card_cash_advance", "credit_card_cash_advance")


@app.post("/limit")
def limit(body: LimitIn) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        row = conn.execute("UPDATE bank.accounts SET limit_per_txn=%s, limit_per_day=%s WHERE id=%s RETURNING id", (body.limit_per_txn, body.limit_per_day, body.account_id)).fetchone()
        if row is None:
            raise HTTPException(404, "không có tài khoản")
        change_id = f"L-{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO bank.products (id, account_id, amount, kind, ts, channel) VALUES (%s,%s,%s,'limit_raise',%s,'app')",
            (change_id, body.account_id, body.limit_per_txn, now),
        )
        conn.commit()
    post_ledger(
        {
            "event_id": f"account_change:{change_id}",
            "type": "account_change",
            "ts": now.isoformat(),
            "account_hash": account_hash(body.account_id),
            "change_type": "limit_raise",
        }
    )
    return {"id": change_id, "status": "done"}


def _ensure_guardian_consent(conn) -> None:
    conn.execute("ALTER TABLE bank.guardians ADD COLUMN IF NOT EXISTS consent boolean NOT NULL DEFAULT false")


def _guardian_consented(conn, account_id: str) -> bool:
    _ensure_guardian_consent(conn)
    row = conn.execute("SELECT consent FROM bank.guardians WHERE account_id = %s", (account_id,)).fetchone()
    return bool(row and row["consent"])


@app.get("/config")
def bank_config() -> dict:
    return {"purpose_amount_vnd": PURPOSE_AMOUNT_VND}


@app.post("/decisions/{decision_id}/choice")
def record_choice(decision_id: str, body: ChoiceIn) -> dict:
    now = CLOCK.now()
    if body.response_ms < 0:
        raise HTTPException(400, "response_ms không hợp lệ")
    try:
        engine_post(f"/decisions/{decision_id}/choice", {"choice": body.choice, "response_ms": body.response_ms})
    except EngineUnavailable as exc:
        log.warning("choice not logged: %s", exc)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        log.warning("choice not logged: %s", exc.detail)
    with connect() as conn:
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE decision_id = %s", (decision_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, "không có quyết định")
        if transfer["status"] == "awaiting_confirmation":
            raise HTTPException(409, "lệnh mức 1 đang chờ xác nhận")
        funds = transfer["funds_status"]
        if body.choice == "cancel":
            funds = "cancelled"
            status = "cancelled"
        elif body.choice == "continue":
            funds = "released"
            status = "done"
        else:
            status = "pending_draft"
        apply_funds(conn, transfer, transfer["funds_status"], funds, now)
        conn.execute(
            "UPDATE bank.transfers SET funds_status=%s, status=%s WHERE id=%s",
            (funds, status, transfer["id"]),
        )
        sync_entry(conn, transfer["id"], transfer["from_account"], funds, status)
        conn.commit()
    return {"decision_id": decision_id, "choice": body.choice, "logged": True, "status": status, "funds_status": funds}


@app.post("/guardian")
def guardian(body: GuardianIn) -> dict:
    if body.consent is not True:
        raise HTTPException(400, "cần sự đồng ý của khách")
    phone_hash = body.guardian_phone_hash or (account_hash(body.phone.strip()) if body.phone and body.phone.strip() else None)
    if not phone_hash:
        raise HTTPException(400, "thiếu số điện thoại")
    now = CLOCK.now()
    with connect() as conn:
        found = conn.execute("SELECT 1 FROM bank.accounts WHERE id = %s", (body.account_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "không có tài khoản")
        _ensure_guardian_consent(conn)
        conn.execute(
            """
            INSERT INTO bank.guardians (account_id, guardian_phone_hash, relation, registered_at, consent)
            VALUES (%s,%s,%s,%s,true)
            ON CONFLICT (account_id) DO UPDATE
            SET guardian_phone_hash=EXCLUDED.guardian_phone_hash, relation=EXCLUDED.relation, consent=true
            """,
            (body.account_id, phone_hash, body.relation, now),
        )
        conn.execute("UPDATE bank.accounts SET has_guardian = true WHERE id = %s", (body.account_id,))
        conn.commit()
    return {"account_id": body.account_id, "status": "registered"}


@app.get("/guardian/requests")
def guardian_requests() -> dict:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.account_id, r.transfer_id, r.level, r.funds_status,
                   t.amount, t.to_account, a.holder_name AS sender_name
            FROM bank.guardian_requests r
            LEFT JOIN bank.transfers t ON t.id = r.transfer_id
            LEFT JOIN bank.accounts a ON a.id = r.account_id
            WHERE r.status = 'open'
            ORDER BY r.id
            """
        ).fetchall()
    return {
        "requests": [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "transfer_id": row["transfer_id"],
                "level": numeric_level(row["level"]),
                "funds_status": row["funds_status"],
                "amount": None if row["amount"] is None else int(row["amount"]),
                "to_account": row["to_account"],
                "sender_name": row["sender_name"],
            }
            for row in rows
        ]
    }


@app.post("/guardian/requests/{request_id}/confirm")
def confirm_guardian(request_id: str) -> dict:
    now = CLOCK.now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM bank.guardian_requests WHERE id = %s", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "không có yêu cầu")
        transfer = conn.execute("SELECT * FROM bank.transfers WHERE id = %s", (row["transfer_id"],)).fetchone()
        answered = engine_post("/respond", {"decision_id": transfer["decision_id"], "guardian_confirm": True, "ts": now.isoformat()})
        apply_funds(conn, transfer, transfer["funds_status"], answered["funds_status"], now)
        level = numeric_level(answered["level"])
        released_status = status_of(level, answered["funds_status"], transfer["hung_up"])
        conn.execute(
            "UPDATE bank.transfers SET funds_status=%s, status=%s, level=%s WHERE id=%s",
            (answered["funds_status"], released_status, str(level), transfer["id"]),
        )
        sync_entry(conn, transfer["id"], transfer["from_account"], answered["funds_status"], released_status)
        conn.execute("UPDATE bank.guardian_requests SET status='confirmed', funds_status='released' WHERE id=%s", (request_id,))
        conn.commit()
    return {"id": request_id, "funds_status": "released"}


if __name__ == "__main__":
    import uvicorn

    require_key()
    uvicorn.run(app, host="0.0.0.0", port=8080)
