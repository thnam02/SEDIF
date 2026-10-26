"""Plugin đặc trưng trên Redis. Cửa sổ là sorted set theo microsecond. get() không đọc Postgres."""

from __future__ import annotations

import os
import time

import redis

PRODUCT_TX = {"savings_liquidation", "quick_loan_disbursement", "credit_card_cash_advance"}

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
    def __init__(self, url: str | None = None, flush: bool = True):
        target = url or os.environ.get("REDIS_URL", "redis://localhost:6379/15")
        if target.rstrip("/").endswith("/0"):
            raise RuntimeError("không xoá Redis db 0")
        self.redis = redis.Redis.from_url(target, decode_responses=True)
        self.redis.ping()
        if flush:
            self.redis.flushdb()
        self.hydrate = not flush
        self.pipe = self.redis.pipeline(transaction=False)
        self.seq = int(self.redis.get("f:seq") or 0) if self.hydrate else 0
        self.first: dict[tuple[str, str], tuple[int, int, int]] = {}
        self.cust_first: set[tuple[str, str]] = set()
        self.main: dict[str, tuple[int, int, str]] = {}
        self.signaled: set[str] = set()
        self.session_account: dict[str, str] = {}
        self.customer_of: dict[str, str] = {}
        self.limit_of: dict[str, int] = {}
        self.listed_at: dict[str, int] = {}
        self.get_times: list[float] = []

    def flush(self) -> None:
        self.pipe.set("f:seq", self.seq)
        if len(self.pipe.command_stack) if hasattr(self.pipe, "command_stack") else True:
            self.pipe.execute()
        self.pipe = self.redis.pipeline(transaction=False)

    def _cached_first(self, account: str, payee: str):
        key = (account, payee)
        if key in self.first:
            return self.first[key]
        if not self.hydrate:
            return None
        raw = self.redis.hget(f"f:fst:{account}", payee)
        if not raw:
            return None
        seq, stamp, xfer = raw.split("|")
        self.first[key] = (int(seq), int(stamp), int(xfer))
        return self.first[key]

    def _ensure_party(self, account: str) -> None:
        if account in self.customer_of or not self.hydrate:
            return
        meta = self.redis.hgetall(f"f:meta:{account}")
        if not meta:
            return
        self.customer_of[account] = meta.get("customer", "")
        self.limit_of[account] = int(meta.get("limit") or 0)

    def _flush_often(self) -> None:
        pending = len(getattr(self.pipe, "command_stack", []))
        if pending >= 2000:
            self.flush()

    def update(self, event: dict) -> None:
        kind = event["type"]
        if kind == "account":
            self._account(event)
        elif kind == "session":
            self._session(event)
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
                "segment": event.get("segment", "thường"),
                "guardian": int(event.get("has_guardian", 0)),
                "limit": int(event.get("limit", 0)),
            },
        )
        if event.get("customer"):
            self.pipe.sadd(f"f:cacc:{event['customer']}", event["account"])
        self.customer_of[event["account"]] = event["customer"]
        self.limit_of[event["account"]] = int(event.get("limit", 0))

    def _session(self, event: dict) -> None:
        self.session_account[event["session"]] = event["account"]
        self.pipe.hset(
            f"f:sess:{event['session']}",
            mapping={
                "start": int(event["start"]),
                "end": int(event["end"]),
                "account": event["account"],
                "customer": event.get("customer", ""),
            },
        )
        self.pipe.zadd(f"f:by:{event['account']}", {event["session"]: int(event["end"]) + 1})
        if event.get("first_input"):
            self.pipe.set(f"f:infirst:{event['session']}", event["first_input"])

    def session_at(self, sender: str, ts: int) -> str | None:
        self.flush()
        found = self.redis.zrangebyscore(f"f:by:{sender}", int(ts), int(ts))
        if not found:
            return None
        return str(found[-1])

    def _link(self, event: dict) -> None:
        seq = self._next()
        self.pipe.hset(f"f:lk:{seq}", mapping={"dev": event["device"], "acct": event["account"]})
        self.pipe.zadd(f"f:al:{event['account']}", {str(seq): event["ts"]})
        self.pipe.zadd(f"f:dl:{event['device']}", {str(seq): event["ts"]})

    def _listing_ts(self, payee: str) -> int | None:
        if payee in self.listed_at:
            return self.listed_at[payee]
        if not self.hydrate:
            return None
        found = self.redis.zrange(f"f:simo:{payee}", 0, 0, withscores=True)
        if not found:
            return None
        self.listed_at[payee] = int(found[0][1])
        return self.listed_at[payee]

    def _simo(self, event: dict) -> None:
        stamp = int(event["ts"])
        previous = self.listed_at.get(event["account"])
        if previous is None or stamp < previous:
            self.listed_at[event["account"]] = stamp
        self.pipe.zadd(f"f:simo:{event['account']}", {str(self._next()): event["ts"]})

    def _txn(self, event: dict) -> None:
        seq = self._next()
        payee = event["payee"] or "-"
        session = event["session"] or "-"
        member = f"{event['ts']}|{seq}|{payee}|{event['amount']}|{event['balance']}|{int(event['xfer'])}|{session}|{event['tx_type']}"
        self.pipe.zadd(f"f:out:{event['account']}", {member: event["ts"]})
        known = bool(event.get("known_payee", event["payee"] is not None))
        if event["xfer"]:
            self.pipe.zadd(f"f:xf:{event['account']}", {f"{event['ts']}|{seq}|{event['amount']}": event["ts"]})
        if event["payee"] and known:
            self.pipe.zadd(
                f"f:in:{event['payee']}",
                {f"{event['ts']}|{seq}|{event['account']}|{event['amount']}": event["ts"]},
            )
            key = (event["account"], event["payee"])
            if self._cached_first(event["account"], event["payee"]) is None and key not in self.first:
                self.first[key] = (seq, int(event["ts"]), int(event["xfer"]))
                self.pipe.hset(f"f:fst:{event['account']}", event["payee"], f"{seq}|{event['ts']}|{int(event['xfer'])}")
                if event["xfer"]:
                    self.pipe.zadd(f"f:fresh:{event['account']}", {event["payee"]: event["ts"]})
        if event["payee"]:
            self._ensure_party(event["account"])
            customer = self.customer_of.get(event["account"], "")
            pair = (customer, event["payee"])
            if customer and pair not in self.cust_first:
                prior = self.redis.hget(f"f:fstc:{customer}", event["payee"]) if self.hydrate else None
                if prior:
                    self.cust_first.add(pair)
                else:
                    self.cust_first.add(pair)
                    stamp = int(event["ts"])
                    self.pipe.hset(f"f:fstc:{customer}", event["payee"], stamp)
            if event["xfer"] and known:
                owner = customer or event["account"]
                listed = self._listing_ts(event["payee"])
                if listed is None or int(event["ts"]) < listed:
                    self.pipe.hincrby(f"f:prelist:{owner}", event["payee"], 1)
                    self.pipe.hsetnx(f"f:prefirst:{owner}", event["payee"], int(event["ts"]))
        if event["xfer"] and event["payee"]:
            limit = self.limit_of.get(event["account"], 0)
            if event["session"] and limit > 0 and int(event["amount"]) >= 0.85 * limit:
                self.pipe.incr(f"f:large:{event['session']}")
            self._main(event)
        owner = self.session_account.get(event["session"])
        if event["xfer"] and event["payee"] and known and owner == event["account"]:
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

    def _main(self, event: dict) -> None:
        session = event["session"]
        if not session:
            return
        if session not in self.main and self.hydrate:
            raw = self.redis.hgetall(f"f:main:{session}")
            if raw:
                self.main[session] = (int(raw["amount"]), int(raw["ts"]), raw["payee"])
        amount = int(event["amount"])
        stamp = int(event["ts"])
        current = self.main.get(session)
        if current is not None and (amount < current[0] or (amount == current[0] and stamp < current[1])):
            return
        self.main[session] = (amount, stamp, event["payee"])
        self.pipe.hset(f"f:main:{session}", mapping={"amount": amount, "ts": stamp, "payee": event["payee"]})

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
            value = name.removeprefix("acct_input_")
            self.pipe.set(f"f:input:{session}", value)

    def _signal(self, event: dict) -> None:
        session = event["session"]
        if not session or session in self.signaled:
            return
        if self.hydrate and self.redis.exists(f"f:sig:{session}"):
            self.signaled.add(session)
            return
        self.signaled.add(session)
        self.pipe.hset(
            f"f:sig:{session}",
            mapping={"present": "1", "in_call": event["in_call"], "ended": event["ended"]},
        )

    def forget_runtime(self) -> None:
        """Xoá cache trong tiến trình sau khi nạp lại RDB, và bỏ điểm phiên thử ghi sau snapshot."""
        try:
            self.redis.ping()
        except Exception:
            self.redis.connection_pool.disconnect()
            self.redis.ping()
        self.pipe = self.redis.pipeline(transaction=False)
        self.seq = int(self.redis.get("f:seq") or 0)
        self.first.clear()
        self.cust_first.clear()
        self.main.clear()
        self.signaled.clear()
        self.session_account.clear()
        self.customer_of.clear()
        self.limit_of.clear()
        self.listed_at.clear()
        for pattern in (
            "f:sig:demo-*",
            "f:sess:demo-*",
            "f:infirst:demo-*",
            "f:mob:demo-*",
            "f:input:demo-*",
            "f:paste:demo-*",
            "f:login:demo-*",
            "f:dec:demo-*",
        ):
            for key in self.redis.scan_iter(pattern, count=500):
                self.redis.delete(key)
        for key in self.redis.scan_iter("f:by:*", count=500):
            members = self.redis.zrange(key, 0, -1)
            demo = [item for item in members if str(item).startswith("demo-")]
            if demo:
                self.redis.zrem(key, *demo)
        self._strip_demo_ledger()

    def _population_seq(self) -> int:
        """Seq của điểm f:out muộn nhất. Snapshot diễn ra trước mọi phiên thử."""
        cursor = 0
        best_score = -1
        best_seq = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match="f:out:*", count=400)
            if keys:
                pipe = self.redis.pipeline(transaction=False)
                for key in keys:
                    pipe.zrange(key, -1, -1, withscores=True)
                grouped = pipe.execute()
                for members in grouped:
                    for member, score in members:
                        parts = str(member).split("|")
                        if len(parts) < 2 or not parts[1].isdigit():
                            continue
                        if score > best_score:
                            best_score = score
                            best_seq = int(parts[1])
            if cursor == 0:
                break
        return best_seq

    def _strip_demo_ledger(self) -> None:
        """Bỏ điểm ghi sau snapshot: sổ ra, sản phẩm, đổi hạn mức, lần gặp người nhận."""
        cutoff = self._population_seq()
        if cutoff <= 0:
            return
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match="f:out:*", count=400)
            if keys:
                pipe = self.redis.pipeline(transaction=False)
                for key in keys:
                    pipe.zrange(key, 0, -1)
                grouped = pipe.execute()
                remove = self.redis.pipeline(transaction=False)
                queued = 0
                for key, members in zip(keys, grouped):
                    account = key.removeprefix("f:out:")
                    for member in members:
                        parts = str(member).split("|")
                        if len(parts) < 8 or not parts[1].isdigit() or int(parts[1]) <= cutoff:
                            continue
                        ts, seq, payee, amount, _bal, xfer, _session, tx_type = parts[:8]
                        remove.zrem(key, member)
                        queued += 1
                        if xfer == "1":
                            remove.zrem(f"f:xf:{account}", f"{ts}|{seq}|{amount}")
                        if payee != "-":
                            remove.zrem(f"f:in:{payee}", f"{ts}|{seq}|{account}|{amount}")
                        if tx_type in PRODUCT_TX:
                            remove.zrem(f"f:prod:{tx_type}:{account}", str(int(seq) + 1))
                if queued:
                    remove.execute()
            if cursor == 0:
                break
        self._drop_after(cutoff)

    def _drop_after(self, cutoff: int) -> None:
        for pattern in ("f:prod:*", "f:chg:*"):
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=pattern, count=200)
                if keys:
                    pipe = self.redis.pipeline(transaction=False)
                    for key in keys:
                        pipe.zrange(key, 0, -1)
                    grouped = pipe.execute()
                    remove = self.redis.pipeline(transaction=False)
                    queued = 0
                    for key, members in zip(keys, grouped):
                        late = [item for item in members if str(item).isdigit() and int(item) > cutoff]
                        if late:
                            remove.zrem(key, *late)
                            queued += 1
                    if queued:
                        remove.execute()
                if cursor == 0:
                    break
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match="f:fst:*", count=400)
            if keys:
                pipe = self.redis.pipeline(transaction=False)
                for key in keys:
                    pipe.hgetall(key)
                grouped = pipe.execute()
                remove = self.redis.pipeline(transaction=False)
                queued = 0
                for key, mapping in zip(keys, grouped):
                    late = [payee for payee, raw in mapping.items() if str(raw).split("|", 1)[0].isdigit() and int(str(raw).split("|", 1)[0]) > cutoff]
                    if late:
                        remove.hdel(key, *late)
                        account = key.removeprefix("f:fst:")
                        customer = self.redis.hget(f"f:meta:{account}", "customer")
                        if customer:
                            stamps = self.redis.hmget(f"f:fstc:{customer}", late)
                            for payee, raw, stamp in zip(late, (mapping[item] for item in late), stamps):
                                parts = str(raw).split("|")
                                if stamp is not None and len(parts) > 1 and stamp == parts[1]:
                                    remove.hdel(f"f:fstc:{customer}", payee)
                        queued += 1
                if queued:
                    remove.execute()
            if cursor == 0:
                break

    def memory_bytes(self) -> int:
        self.flush()
        info = self.redis.info("memory")
        return int(info["used_memory"])

    def get(self, sender: str, payee: str | None, ts: int, session_id: str, pending: dict | None = None) -> dict:
        started = time.perf_counter()
        self.flush()
        row = self._read(sender, payee, int(ts), session_id, pending)
        self.get_times.append(time.perf_counter() - started)
        return row

    def _read(self, sender: str, payee: str | None, as_of: int, session_id: str, pending: dict | None = None) -> dict:
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
        if pending:
            decision = {
                "ts": int(pending["ts"]),
                "seq": -1,
                "payee": pending["payee"],
                "amt": int(pending["amount"]),
                "bal": int(pending.get("balance") or 0),
            }
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
        row.update(self._families(sender, session_id, sender_meta, signal, mobilized))
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
        row["a_w"] = self._window_amount(sender, as_of, str(sender_meta.get("customer") or ""))
        row["new_payees_24h"] = self._new_payees(sender, as_of, str(sender_meta.get("customer") or ""))
        customer = str(sender_meta.get("customer") or "")
        first = client.hget(f"f:fstc:{customer}", decision["payee"]) if customer else client.hget(f"f:fst:{sender}", decision["payee"])
        if decision["seq"] < 0:
            if customer:
                row["new_payee"] = 0 if first and int(first) < decision["ts"] else 1
            else:
                row["new_payee"] = 0 if first and int(first.split("|")[1]) < decision["ts"] else 1
        elif customer:
            row["new_payee"] = int(bool(first) and int(first) == decision["ts"])
        else:
            row["new_payee"] = int(bool(first) and int(first.split("|", 1)[0]) == decision["seq"])
        method = client.get(f"f:pm:{sender}:{decision['payee']}")
        row["add_method"] = method if method is not None else None
        self._payee_fields(row, sender, decision["payee"], as_of, sender_meta)
        if pending and (row["new_payee"] or row["simo_listed"]):
            row["a_w"] = int(row["a_w"] or 0) + int(pending["amount"])
        return row

    def _families(self, sender: str, session_id: str, sender_meta: dict, signal: dict, mobilized) -> dict:
        pipe = self.redis.pipeline(transaction=False)
        pipe.hgetall(f"f:sess:{session_id}")
        pipe.get(f"f:infirst:{session_id}")
        pipe.get(f"f:large:{session_id}")
        pipe.hgetall(f"f:main:{session_id}")
        bounds, first_input, large, main = pipe.execute()
        call = bool(signal) and (signal.get("in_call") == "1" or signal.get("ended") == "1")
        entry = first_input == "pasted"
        start = int(bounds.get("start") or 0) if bounds else 0
        end = int(bounds.get("end") or 0) if bounds else 0
        account = (bounds or {}).get("account") or sender
        raised = 0
        if start and end:
            raised = int(self.redis.zcount(f"f:chg:limit_raise:{account}", start - 30 * 60 * 1_000_000, end))
        payee_on = False
        if main and main.get("payee") and start:
            customer = (bounds or {}).get("customer") or (sender_meta or {}).get("customer", "")
            seen = self.redis.hget(f"f:fstc:{customer}", main["payee"]) if customer else None
            payee_on = seen is None or int(seen) >= start
        funding = bool(mobilized) or int(large or 0) >= 2 or raised > 0
        return {
            "call_family": int(call),
            "entry_family": int(entry),
            "payee_family": int(payee_on),
            "funding_family": int(funding),
            "segment": (sender_meta or {}).get("segment", "thường"),
            "has_guardian": int((sender_meta or {}).get("guardian", "0") or 0),
        }

    def trim(self, as_of_us: int) -> None:
        """Bỏ điểm cũ hơn cửa sổ dài nhất, 7 ngày, trên các sorted set trượt."""
        self.flush()
        cutoff = as_of_us - 7 * DAY_US
        prefixes = ("f:out:", "f:in:", "f:xf:", "f:prod:", "f:chg:", "f:fresh:")
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match="f:*", count=1000)
            pipe = self.redis.pipeline(transaction=False)
            queued = 0
            for key in keys:
                if key.startswith(prefixes):
                    pipe.zremrangebyscore(key, "-inf", cutoff)
                    queued += 1
            if queued:
                pipe.execute()
            if cursor == 0:
                break

    def index_customers(self) -> None:
        """Lập tập tài khoản theo khách từ meta đã có, để a_w gộp như khung network."""
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match="f:meta:*", count=500)
            if keys:
                pipe = self.redis.pipeline(transaction=False)
                for key in keys:
                    pipe.hgetall(key)
                metas = pipe.execute()
                write = self.redis.pipeline(transaction=False)
                queued = 0
                for key, meta in zip(keys, metas):
                    customer = meta.get("customer")
                    if customer:
                        write.sadd(f"f:cacc:{customer}", key.removeprefix("f:meta:"))
                        queued += 1
                if queued:
                    write.execute()
            if cursor == 0:
                break

    def _accounts(self, sender: str, customer: str) -> list[str]:
        if not customer:
            return [sender]
        found = self.redis.smembers(f"f:cacc:{customer}")
        return list(found) if found else [sender]

    def _window_amount(self, sender: str, as_of: int, customer: str = "") -> int:
        start = f"({as_of - DAY_US}"
        stop = f"({as_of}"
        chosen = []
        for account in self._accounts(sender, customer):
            members = self.redis.zrangebyscore(f"f:out:{account}", start, stop)
            for member in members:
                ts, _seq, payee, amt, _bal, xfer, _session, _kind = _split(member, 8)
                if xfer != "1" or payee == "-":
                    continue
                chosen.append((account, int(ts), payee, int(amt)))
        if not chosen:
            return 0
        pipe = self.redis.pipeline(transaction=False)
        for account, _ts, payee, _amt in chosen:
            pipe.hget(f"f:fst:{account}", payee)
            if customer:
                pipe.hget(f"f:fstc:{customer}", payee)
            pipe.zcount(f"f:simo:{payee}", "-inf", f"({_ts}")
        fetched = pipe.execute()
        width = 3 if customer else 2
        total = 0
        for index, (_account, ts, _payee, amt) in enumerate(chosen):
            base = index * width
            known = fetched[base]
            if customer:
                stamp = fetched[base + 1]
                listed = int(fetched[base + 2]) > 0
                is_first = bool(known) and bool(stamp) and int(stamp) == ts
            else:
                listed = int(fetched[base + 1]) > 0
                parts = known.split("|") if known else []
                is_first = len(parts) >= 3 and int(parts[1]) == ts and parts[2] == "1"
            if is_first or (listed and known):
                total += amt
        return total

    def _new_payees(self, sender: str, as_of: int, customer: str) -> int:
        start = f"({as_of - DAY_US}"
        stop = f"({as_of}"
        if not customer:
            return int(self.redis.zcount(f"f:fresh:{sender}", start, stop))
        found = set()
        for account in self._accounts(sender, customer):
            payees = self.redis.zrangebyscore(f"f:fresh:{account}", start, stop)
            if not payees:
                continue
            pipe = self.redis.pipeline(transaction=False)
            for payee in payees:
                pipe.zscore(f"f:fresh:{account}", payee)
                pipe.hget(f"f:fstc:{customer}", payee)
            fetched = pipe.execute()
            for index, payee in enumerate(payees):
                score = fetched[index * 2]
                stamp = fetched[index * 2 + 1]
                if score is not None and stamp is not None and int(float(score)) == int(stamp):
                    found.add(payee)
        return len(found)

    def _payee_fields(self, row: dict, sender: str, payee: str, as_of: int, sender_meta: dict) -> None:
        client = self.redis
        pipe = client.pipeline(transaction=False)
        pipe.hgetall(f"f:meta:{payee}")
        pipe.zcount(f"f:simo:{payee}", "-inf", f"({as_of}")
        meta, listed = pipe.execute()
        row["has_payee_signals"] = 1
        row["simo_listed"] = int(int(listed) > 0)
        customer = str(sender_meta.get("customer") or "") if sender_meta else ""
        owner = customer or sender
        if payee:
            fetched = client.pipeline(transaction=False)
            fetched.hget(f"f:prelist:{owner}", payee)
            fetched.hget(f"f:prefirst:{owner}", payee)
            prior, first = fetched.execute()
        else:
            prior, first = None, None
        row["payee_transfers_before_listing"] = int(prior or 0)
        listed_ts = self._listing_ts(payee) if payee else None
        if first and listed_ts is not None and int(listed_ts) > int(first):
            row["payee_first_days_before_listing"] = (int(listed_ts) - int(first)) / DAY_US
        else:
            row["payee_first_days_before_listing"] = 0.0
        opened = int(meta.get("opened", "0") or 0) if meta else 0
        visible = 0 < opened < as_of
        sender_name = str(sender_meta.get("holder", "")) if sender_meta else ""
        payee_name = str(meta.get("holder", "")) if visible and meta else ""
        if sender_name and payee_name:
            row["payee_name_matches_holder"] = int(sender_name == payee_name)
        if visible and meta:
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
