"""Plugin đặc trưng trên Redis. Cửa sổ là sorted set theo microsecond. get() không đọc Postgres."""

from __future__ import annotations

import os
import time

import redis

from scripts.load_sim_events import PRODUCT_TX

DAY_US = 86_400 * 1_000_000
DAY_NS = 86_400 * 1_000_000_000
TZ_OFFSET_S = 7 * 3600
ACCT_INPUT = ("typed", "typed_chunked", "pasted", "chosen")
COLUMNS = (
    "amount",
    "amount_to_balance",
    "a_w",
    "new_payee",
    "add_method",
    "hour",
    "seconds_since_login",
    "new_payees_24h",
    "has_tx_signals",
    "payee_name_matches_holder",
    "merchant_verified",
    "has_payee_signals",
    "product_savings_7d",
    "product_loan_7d",
    "product_cash_advance_7d",
    "limit_raise_1d",
    "phone_change_7d",
    "pasted",
    "has_device_signals",
    "payee_account_age_days",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
    "payee_account_age_days_missing",
    "distinct_senders_7d_missing",
    "passthrough_24h_missing",
    "device_holder_count_missing",
    "simo_listed",
)
FAMILY_COLUMNS = ("in_call", "call_ended_in_session", "acct_input", "mobilized")
PRODUCT_COLUMN = {
    "savings_liquidation": "product_savings_7d",
    "quick_loan_disbursement": "product_loan_7d",
    "credit_card_cash_advance": "product_cash_advance_7d",
}


def _split(member: str, count: int) -> list[str]:
    return member.split("|", count - 1)


class OnlineStore:
    def __init__(self, url: str | None = None):
        target = url or os.environ.get("REDIS_URL", "redis://localhost:6379/15")
        if target.rstrip("/").endswith("/0"):
            raise RuntimeError("không xoá Redis db 0")
        self.redis = redis.Redis.from_url(target, decode_responses=True)
        self.redis.ping()
        self.redis.flushdb()
        self.pipe = self.redis.pipeline(transaction=False)
        self.seq = 0
        self.first: dict[tuple[str, str], tuple[int, int, int]] = {}
        self.signaled: set[str] = set()
        self.session_account: dict[str, str] = {}
        self.get_times: list[float] = []

    def flush(self) -> None:
        if len(self.pipe.command_stack) if hasattr(self.pipe, "command_stack") else True:
            self.pipe.execute()
        self.pipe = self.redis.pipeline(transaction=False)

    def _flush_often(self) -> None:
        pending = len(getattr(self.pipe, "command_stack", []))
        if pending >= 2000:
            self.flush()

    def update(self, event: dict) -> None:
        kind = event["type"]
        if kind == "account":
            self._account(event)
        elif kind == "device_link":
            self._link(event)
        elif kind == "simo_listing":
            self._simo(event)
        elif kind == "txn":
            self._txn(event)
        elif kind == "product":
            self._product(event)
        elif kind == "account_change":
            self._change(event)
        elif kind == "payee_add":
            self._payee_add(event)
        elif kind == "session_event":
            self._session_event(event)
        elif kind == "device_signal":
            self._signal(event)
        self._flush_often()

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def _account(self, event: dict) -> None:
        self.pipe.hset(
            f"f:meta:{event['account']}",
            mapping={
                "opened": int(event["opened"]),
                "holder": event["holder"],
                "merchant": int(event["merchant"]),
                "b01": int(event["b01"]),
                "customer": event["customer"],
            },
        )

    def _link(self, event: dict) -> None:
        seq = self._next()
        self.pipe.hset(f"f:lk:{seq}", mapping={"dev": event["device"], "acct": event["account"]})
        self.pipe.zadd(f"f:al:{event['account']}", {str(seq): event["ts"]})
        self.pipe.zadd(f"f:dl:{event['device']}", {str(seq): event["ts"]})

    def _simo(self, event: dict) -> None:
        self.pipe.zadd(f"f:simo:{event['account']}", {str(self._next()): event["ts"]})

    def _txn(self, event: dict) -> None:
        seq = self._next()
        payee = event["payee"] or "-"
        session = event["session"] or "-"
        member = f"{event['ts']}|{seq}|{payee}|{event['amount']}|{event['balance']}|{int(event['xfer'])}|{session}|{event['tx_type']}"
        self.pipe.zadd(f"f:out:{event['account']}", {member: event["ts"]})
        if event["xfer"]:
            self.pipe.zadd(f"f:xf:{event['account']}", {f"{event['ts']}|{seq}|{event['amount']}": event["ts"]})
        if event["payee"]:
            self.pipe.zadd(
                f"f:in:{event['payee']}",
                {f"{event['ts']}|{seq}|{event['account']}|{event['amount']}": event["ts"]},
            )
            key = (event["account"], event["payee"])
            if key not in self.first:
                self.first[key] = (seq, int(event["ts"]), int(event["xfer"]))
                self.pipe.hset(f"f:fst:{event['account']}", event["payee"], f"{seq}|{event['ts']}|{int(event['xfer'])}")
                if event["xfer"]:
                    self.pipe.zadd(f"f:fresh:{event['account']}", {event["payee"]: event["ts"]})
        owner = self.session_account.get(event["session"])
        if event["xfer"] and event["payee"] and owner == event["account"]:
            self.pipe.hset(
                f"f:dec:{event['session']}",
                mapping={
                    "ts": int(event["ts"]),
                    "seq": seq,
                    "payee": event["payee"],
                    "amt": int(event["amount"]),
                    "bal": int(event["balance"]),
                },
            )
        if event["tx_type"] in PRODUCT_TX:
            self._product(
                {
                    "product_type": event["tx_type"],
                    "account": event["account"],
                    "ts": event["ts"],
                    "session": event["session"],
                }
            )

    def _product(self, event: dict) -> None:
        kind = event["product_type"]
        if kind not in PRODUCT_TX:
            return
        self.pipe.zadd(f"f:prod:{kind}:{event['account']}", {str(self._next()): event["ts"]})
        if event.get("session"):
            self.pipe.set(f"f:mob:{event['session']}", "1")

    def _change(self, event: dict) -> None:
        self.pipe.zadd(f"f:chg:{event['change_type']}:{event['account']}", {str(self._next()): event["ts"]})

    def _payee_add(self, event: dict) -> None:
        self.pipe.set(f"f:pm:{event['account']}:{event['payee']}", event["method"])

    def _session_event(self, event: dict) -> None:
        name = event["event_type"]
        session = event["session"]
        if not session:
            return
        if name == "login":
            self.pipe.set(f"f:login:{session}", event["ts"], nx=True)
        elif name == "paste_account":
            self.pipe.set(f"f:paste:{session}", "1")
        elif name.startswith("acct_input_"):
            self.pipe.set(f"f:input:{session}", name.removeprefix("acct_input_"))

    def _signal(self, event: dict) -> None:
        session = event["session"]
        if not session or session in self.signaled:
            return
        self.signaled.add(session)
        self.pipe.hset(
            f"f:sig:{session}",
            mapping={"present": "1", "in_call": event["in_call"], "ended": event["ended"]},
        )

    def memory_bytes(self) -> int:
        self.flush()
        info = self.redis.info("memory")
        return int(info["used_memory"])

    def get(self, sender: str, payee: str | None, ts: int, session_id: str) -> dict:
        started = time.perf_counter()
        self.flush()
        row = self._read(sender, payee, int(ts), session_id)
        self.get_times.append(time.perf_counter() - started)
        return row

    def _read(self, sender: str, payee: str | None, as_of: int, session_id: str) -> dict:
        client = self.redis
        pipe = client.pipeline(transaction=False)
        pipe.hgetall(f"f:dec:{session_id}")
        pipe.get(f"f:paste:{session_id}")
        pipe.get(f"f:login:{session_id}")
        pipe.get(f"f:input:{session_id}")
        pipe.get(f"f:mob:{session_id}")
        pipe.hgetall(f"f:sig:{session_id}")
        pipe.hgetall(f"f:meta:{sender}")
        for kind in PRODUCT_COLUMN:
            pipe.zcount(f"f:prod:{kind}:{sender}", f"({as_of - 7 * DAY_US}", f"({as_of}")
        pipe.zcount(f"f:chg:limit_raise:{sender}", f"({as_of - DAY_US}", f"({as_of}")
        pipe.zcount(f"f:chg:phone_change:{sender}", f"({as_of - 7 * DAY_US}", f"({as_of}")
        (
            decision_raw,
            pasted,
            login,
            acct_input,
            mobilized,
            signal,
            sender_meta,
            savings,
            loan,
            cash,
            raises,
            phones,
        ) = pipe.execute()
        decision = _decision(decision_raw)
        counterparty = decision["payee"] if decision else payee
        row = {name: None for name in (*COLUMNS, *FAMILY_COLUMNS)}
        row["new_payee"] = 0
        row["pasted"] = 0
        row["has_tx_signals"] = 0
        row["has_payee_signals"] = 0
        row["simo_listed"] = 0
        row["has_device_signals"] = 1 if signal else 0
        row["product_savings_7d"] = int(savings)
        row["product_loan_7d"] = int(loan)
        row["product_cash_advance_7d"] = int(cash)
        row["limit_raise_1d"] = int(raises)
        row["phone_change_7d"] = int(phones)
        row["in_call"] = _tri(signal.get("in_call")) if signal else None
        row["call_ended_in_session"] = _tri(signal.get("ended")) if signal else None
        row["acct_input"] = acct_input if acct_input in ACCT_INPUT else None
        row["mobilized"] = 1 if mobilized else 0
        if decision is None:
            _t4c_missing(row)
            return row
        row["has_tx_signals"] = 1
        row["amount"] = decision["amt"]
        row["amount_to_balance"] = decision["amt"] / decision["bal"] if decision["bal"] > 0 else None
        row["hour"] = int(((decision["ts"] // 1_000_000) + TZ_OFFSET_S) % 86400 // 3600)
        row["pasted"] = 1 if pasted else 0
        if login is not None and decision["ts"] >= int(login):
            row["seconds_since_login"] = (decision["ts"] - int(login)) / 1_000_000
        row["a_w"] = self._window_amount(sender, as_of)
        row["new_payees_24h"] = int(client.zcount(f"f:fresh:{sender}", f"({as_of - DAY_US}", f"({as_of}"))
        first = client.hget(f"f:fst:{sender}", decision["payee"])
        row["new_payee"] = int(bool(first) and int(first.split("|", 1)[0]) == decision["seq"])
        method = client.get(f"f:pm:{sender}:{decision['payee']}")
        row["add_method"] = method if method is not None else None
        self._payee_fields(row, sender, decision["payee"], as_of, sender_meta)
        return row

    def _window_amount(self, sender: str, as_of: int) -> int:
        members = self.redis.zrangebyscore(f"f:out:{sender}", f"({as_of - DAY_US}", f"({as_of}")
        chosen = []
        pipe = self.redis.pipeline(transaction=False)
        for member in members:
            ts, seq, payee, amt, _bal, xfer, _session, _kind = _split(member, 8)
            if xfer != "1" or payee == "-":
                continue
            chosen.append((int(seq), payee, int(amt), int(ts)))
            pipe.hget(f"f:fst:{sender}", payee)
            pipe.zcount(f"f:simo:{payee}", "-inf", f"({int(ts)}")
        if not chosen:
            return 0
        fetched = pipe.execute()
        total = 0
        for index, (seq, _payee, amt, _ts) in enumerate(chosen):
            first = fetched[index * 2]
            listed = int(fetched[index * 2 + 1]) > 0
            is_first = bool(first) and int(first.split("|", 1)[0]) == seq
            if is_first or listed:
                total += amt
        return total

    def _payee_fields(self, row: dict, sender: str, payee: str, as_of: int, sender_meta: dict) -> None:
        client = self.redis
        pipe = client.pipeline(transaction=False)
        pipe.hgetall(f"f:meta:{payee}")
        pipe.zcount(f"f:simo:{payee}", "-inf", f"({as_of}")
        meta, listed = pipe.execute()
        row["has_payee_signals"] = 1
        row["simo_listed"] = int(int(listed) > 0)
        opened = int(meta.get("opened", "0") or 0) if meta else 0
        visible = 0 < opened < as_of
        sender_name = str(sender_meta.get("holder", "")) if sender_meta else ""
        payee_name = str(meta.get("holder", "")) if visible and meta else ""
        if sender_name and payee_name:
            row["payee_name_matches_holder"] = int(sender_name == payee_name)
        if visible and meta and int(meta.get("b01", "0") or 0):
            row["merchant_verified"] = int(meta.get("merchant", "0") or 0)
        if not visible:
            _t4c_missing(row)
            return
        row["payee_account_age_days"] = ((as_of - opened) * 1000) / DAY_NS
        row["distinct_senders_7d"] = self._senders(payee, as_of)
        row["passthrough_24h"] = self._passthrough(payee, as_of)
        row["device_holder_count"] = self._holders(payee, as_of)
        _t4c_missing(row)

    def _senders(self, payee: str, as_of: int) -> int:
        members = self.redis.zrangebyscore(f"f:in:{payee}", f"({as_of - 7 * DAY_US}", f"({as_of}")
        return len({_split(member, 4)[2] for member in members})

    def _passthrough(self, payee: str, as_of: int) -> float | None:
        inbound = self.redis.zrangebyscore(f"f:in:{payee}", f"({as_of - 2 * DAY_US}", as_of - DAY_US)
        usable = []
        pipe = self.redis.pipeline(transaction=False)
        for member in inbound:
            ts, _seq, _frm, amt = _split(member, 4)
            amount = int(amt)
            if amount <= 0:
                continue
            stamp = int(ts)
            cap = min(stamp + DAY_US, as_of)
            usable.append(amount)
            pipe.zrangebyscore(f"f:xf:{payee}", f"({stamp}", cap)
        if not usable:
            return None
        windows = pipe.execute()
        ratios = []
        for amount, members in zip(usable, windows):
            moved = 0
            for member in members:
                moved += int(_split(member, 3)[2])
            ratios.append(min(1.0, moved / amount))
        return float(sum(ratios) / len(ratios)) if ratios else None

    def _holders(self, payee: str, as_of: int) -> int | None:
        latest = self.redis.zrevrangebyscore(f"f:al:{payee}", f"({as_of}", "-inf", start=0, num=1)
        if not latest:
            return None
        device = self.redis.hget(f"f:lk:{latest[0]}", "dev")
        if not device:
            return None
        seqs = self.redis.zrangebyscore(f"f:dl:{device}", "-inf", f"({as_of}")
        if not seqs:
            return None
        pipe = self.redis.pipeline(transaction=False)
        for seq in seqs:
            pipe.hget(f"f:lk:{seq}", "acct")
        accounts = pipe.execute()
        pipe = self.redis.pipeline(transaction=False)
        for account in accounts:
            if account:
                pipe.hget(f"f:meta:{account}", "customer")
        customers = {item for item in pipe.execute() if item}
        return len(customers)


def _decision(raw: dict) -> dict | None:
    if not raw or "payee" not in raw:
        return None
    return {
        "ts": int(raw["ts"]),
        "seq": int(raw["seq"]),
        "payee": raw["payee"],
        "amt": int(raw["amt"]),
        "bal": int(raw["bal"]),
    }


def _tri(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _t4c_missing(row: dict) -> None:
    for name in ("payee_account_age_days", "distinct_senders_7d", "passthrough_24h", "device_holder_count"):
        row[f"{name}_missing"] = int(row.get(name) is None)
