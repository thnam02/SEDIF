"""Nạp bảng đã quan sát và tính đặc trưng tại một thời điểm."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "amount",
    "amount_to_balance",
    "a_w",
    "new_payee",
    "pasted",
    "add_method",
    "hour",
    "seconds_since_login",
    "new_payees_24h",
    "new_payees_7d",
    "new_payees_30d",
    "cusum_out_30d",
    "amount_rise_run",
    "balance_out_ratio_30d",
    "has_tx_signals",
    "payee_name_matches_holder",
    "merchant_verified",
    "simo_listed",
    "payee_account_age_days",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
    "has_payee_signals",
    "in_call_audio_mode",
    "accessibility_new",
    "remote_access_app",
    "screen_sharing",
    "overlay",
    "new_device",
    "has_device_signals",
    "on_call",
    "recent_call_minutes_ago",
    "counterparty_is_new",
    "counterparty_number_age_days",
    "counterparty_report_count",
    "has_telco_signals",
]
LONG_TERM = [
    "new_payees_7d",
    "new_payees_30d",
    "cusum_out_30d",
    "amount_rise_run",
    "balance_out_ratio_30d",
]
NETWORK_ONLY = [
    "payee_account_age_days",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
]
_TX_SIGNALS = [
    "amount",
    "amount_to_balance",
    "a_w",
    "new_payee",
    "pasted",
    "add_method",
    "hour",
    "seconds_since_login",
    "new_payees_24h",
    "new_payees_7d",
    "new_payees_30d",
    "cusum_out_30d",
    "amount_rise_run",
    "balance_out_ratio_30d",
    "has_tx_signals",
    "payee_name_matches_holder",
    "merchant_verified",
    "simo_listed",
    "has_payee_signals",
]
_DEVICE_SIGNALS = [
    "in_call_audio_mode",
    "accessibility_new",
    "remote_access_app",
    "screen_sharing",
    "overlay",
    "new_device",
    "has_device_signals",
]
_TELCO_SIGNALS = [
    "on_call",
    "recent_call_minutes_ago",
    "counterparty_is_new",
    "counterparty_number_age_days",
    "counterparty_report_count",
    "has_telco_signals",
]
PROFILES = {
    "basic": frozenset(_TX_SIGNALS),
    "standard": frozenset([*_TX_SIGNALS, *_DEVICE_SIGNALS]),
    "advanced": frozenset([*_TX_SIGNALS, *_DEVICE_SIGNALS, *_TELCO_SIGNALS, *NETWORK_ONLY]),
}

TZ_OFFSET_S = 7 * 3600
NS = 1_000_000_000
DAY_NS = 86_400 * NS
HOUR_NS = 3600 * NS
TRANSFER_TYPES = {
    "transfer",
    "rent",
    "school_fee",
    "large_oneoff",
    "own_transfer",
    "own_transfer_drain",
    "passthrough_out",
}


def _ns(series: pd.Series) -> np.ndarray:
    values = pd.to_datetime(series, utc=True)
    out = np.full(len(series), np.iinfo(np.int64).min, dtype=np.int64)
    ok = values.notna().to_numpy()
    if not ok.any():
        return out
    stamps = values[ok]
    if getattr(stamps.dtype, "unit", "ns") != "ns":
        stamps = stamps.dt.as_unit("ns")
    out[ok] = stamps.astype("int64").to_numpy()
    return out


def _text(series: pd.Series) -> np.ndarray:
    values = series.astype("string").fillna("").to_numpy()
    return np.array(["" if item in {"<NA>", "nan", "None"} else str(item) for item in values], dtype=object)


class using_store:
    _stack: list[FeatureStore] = []

    def __init__(self, store: FeatureStore):
        self.store = store
        self._prev: FeatureStore | None = None

    def __enter__(self) -> FeatureStore:
        using_store._stack.append(self.store)
        return self.store

    def __exit__(self, *_) -> None:
        using_store._stack.pop()

    @classmethod
    def current(cls) -> FeatureStore:
        if not cls._stack:
            raise RuntimeError("Chưa mở FeatureStore")
        return cls._stack[-1]


class FeatureStore:
    def __init__(self, observed: Path | str):
        observed = Path(observed)
        frames = {
            "sessions": pd.read_parquet(observed / "sessions.parquet"),
            "events": pd.read_parquet(observed / "session_events.parquet"),
            "signals": pd.read_parquet(observed / "device_signals.parquet"),
            "transactions": pd.read_parquet(
                observed / "transactions.parquet",
                columns=["tx_id", "ts", "from_account", "to_account", "amount", "tx_type", "session_id", "from_balance_before"],
            ),
            "calls": pd.read_parquet(observed / "call_events.parquet"),
            "accounts": pd.read_parquet(
                observed / "accounts.parquet",
                columns=["account_id", "customer_id", "bank_id", "holder_name_ascii", "opened_at", "merchant_verified"],
            ),
            "payees": pd.read_parquet(observed / "payees.parquet"),
            "simo": pd.read_parquet(observed / "simo_list.parquet"),
            "phones": pd.read_parquet(
                observed / "phone_numbers.parquet",
                columns=["number_id", "registered_at", "reported_count"],
            ),
            "number_reports": pd.read_parquet(observed / "number_reports.parquet"),
            "links": pd.read_parquet(observed / "account_device.parquet"),
            "devices": pd.read_parquet(observed / "devices.parquet", columns=["device_id", "first_seen"]),
        }
        self.contacts: dict[tuple[str, str], int] | None = None
        self._index(frames)

    def attach_contacts(self, contacts: pd.DataFrame) -> None:
        """Sổ danh bạ do bên gọi truyền vào. first_contact muộn hơn as_of_ts bị bỏ lúc tính."""
        first = _ns(contacts["first_contact"])
        customers = _text(contacts["customer_id"])
        numbers = _text(contacts["number_id"])
        book: dict[tuple[str, str], int] = {}
        for customer, number, stamp in zip(customers, numbers, first):
            if not customer or not number or stamp == np.iinfo(np.int64).min:
                continue
            key = (customer, number)
            book[key] = stamp if key not in book else min(book[key], int(stamp))
        self.contacts = book

    def before(self, as_of_ts) -> FeatureStore:
        """Bản sao vật lý, chỉ còn sự kiện có mốc thời gian < as_of_ts."""
        limit = _as_ns(as_of_ts)
        child = FeatureStore.__new__(FeatureStore)
        child.contacts = None if self.contacts is None else {
            key: stamp for key, stamp in self.contacts.items() if stamp < limit
        }
        child.account_id = self.account_id
        child.customer_id = self.customer_id
        child.device_id = self.device_id
        child.session_id = self.session_id
        child.number_id = self.number_id
        child.bank_b01 = self.bank_b01.copy()
        child.holder = self.holder.copy()
        child.merchant = self.merchant.copy()
        child.opened = self.opened.copy()
        hidden = child.opened >= limit
        child.opened[hidden] = np.iinfo(np.int64).max
        child.holder[hidden] = ""
        child.merchant[hidden] = False
        child.customer_of = self.customer_of.copy()
        child.accounts_of = self.accounts_of
        child.reported_count = self.reported_count
        child.registered = self.registered.copy()
        child.registered[child.registered >= limit] = np.iinfo(np.int64).max
        child.device_first = self.device_first.copy()
        child.device_first[child.device_first >= limit] = np.iinfo(np.int64).max
        child._clip_grouped(self, limit)
        return child

    def _clip_grouped(self, parent: FeatureStore, limit: int) -> None:
        def clip(ts: np.ndarray, *cols: np.ndarray) -> tuple[np.ndarray, ...]:
            keep = ts < limit
            return (ts[keep], *(col[keep] for col in cols))

        self.out_ts, self.out_to, self.out_amt, self.out_bal, self.out_sess, self.out_xfer, self.out_acc = clip(
            parent.out_ts, parent.out_to, parent.out_amt, parent.out_bal, parent.out_sess, parent.out_xfer, parent.out_acc
        )
        self.in_ts, self.in_from, self.in_amt, self.in_acc = clip(parent.in_ts, parent.in_from, parent.in_amt, parent.in_acc)
        self.ev_ts, self.ev_type, self.ev_sess = clip(parent.ev_ts, parent.ev_type, parent.ev_sess)
        self.call_ts, self.call_dur, self.call_num, self.call_cust = clip(
            parent.call_ts, parent.call_dur, parent.call_num, parent.call_cust
        )
        self.pay_added, self.pay_method, self.pay_from, self.pay_to = clip(
            parent.pay_added, parent.pay_method, parent.pay_from, parent.pay_to
        )
        self.simo_listed_ns, self.simo_acc = clip(parent.simo_listed_ns, parent.simo_acc)
        self.report_ts, self.report_num = clip(parent.report_ts, parent.report_num)
        self.link_first, self.link_acc, self.link_dev = clip(parent.link_first, parent.link_acc, parent.link_dev)
        self.sig_sess, self.sig_talk, self.sig_remote, self.sig_access, self.sig_share, self.sig_overlay, self.sig_audio = clip(
            parent.sig_sess,
            parent.sig_talk,
            parent.sig_remote,
            parent.sig_access,
            parent.sig_share,
            parent.sig_overlay,
            parent.sig_audio,
        )
        self.sig_access = self.sig_access.copy()
        self.sig_access[self.sig_access >= limit] = np.iinfo(np.int64).min
        self._bounds()
        self.sess_acc = parent.sess_acc
        self.sess_cust = parent.sess_cust
        self.sess_dev = parent.sess_dev
        self.sess_start = parent.sess_start
        # Phiên bắt đầu sau mốc thì không được dùng.
        self.session_of = parent.session_of

    def _index(self, frames: dict[str, pd.DataFrame]) -> None:
        accounts = frames["accounts"].reset_index(drop=True)
        self.account_id = _text(accounts["account_id"])
        self.customer_id = _text(accounts["customer_id"])
        self.bank_b01 = _text(accounts["bank_id"]) == "B01"
        self.holder = _text(accounts["holder_name_ascii"])
        self.merchant = accounts["merchant_verified"].fillna(False).to_numpy(dtype=bool)
        self.opened = _ns(accounts["opened_at"])
        account_code = {item: index for index, item in enumerate(self.account_id)}
        customers = sorted({item for item in self.customer_id if item})
        cust_code = {item: index for index, item in enumerate(customers)}
        self.customer_id = np.array(customers, dtype=object)
        self.customer_of = np.array([cust_code.get(item, -1) for item in _text(accounts["customer_id"])], dtype=np.int32)
        self.accounts_of: dict[int, np.ndarray] = {}
        for code, customer in enumerate(self.customer_of):
            if customer >= 0:
                self.accounts_of.setdefault(int(customer), []).append(code)
        self.accounts_of = {key: np.array(value, dtype=np.int32) for key, value in self.accounts_of.items()}

        devices = frames["devices"].reset_index(drop=True)
        self.device_id = _text(devices["device_id"])
        self.device_first = _ns(devices["first_seen"])
        device_code = {item: index for index, item in enumerate(self.device_id)}

        sessions = frames["sessions"].reset_index(drop=True)
        self.session_id = _text(sessions["session_id"])
        session_code = {item: index for index, item in enumerate(self.session_id)}
        self.sess_acc = np.array([account_code.get(item, -1) for item in _text(sessions["account_id"])], dtype=np.int32)
        self.sess_cust = np.array([cust_code.get(item, -1) for item in _text(sessions["customer_id"])], dtype=np.int32)
        self.sess_dev = np.array([device_code.get(item, -1) for item in _text(sessions["device_id"])], dtype=np.int32)
        self.sess_start = _ns(sessions["start_ts"])
        self.session_of = session_code

        tx = frames["transactions"]
        from_acc = np.array([account_code.get(item, -1) for item in _text(tx["from_account"])], dtype=np.int32)
        to_acc = np.array([account_code.get(item, -1) for item in _text(tx["to_account"])], dtype=np.int32)
        tx_ts = _ns(tx["ts"])
        tx_amt = tx["amount"].fillna(0).to_numpy(dtype=np.int64)
        tx_bal = tx["from_balance_before"].fillna(0).to_numpy(dtype=np.int64)
        tx_sess = np.array([session_code.get(item, -1) for item in _text(tx["session_id"])], dtype=np.int32)
        tx_xfer = np.isin(_text(tx["tx_type"]), list(TRANSFER_TYPES))
        order = np.lexsort((tx_ts, from_acc))
        self.out_acc = from_acc[order]
        self.out_ts = tx_ts[order]
        self.out_to = to_acc[order]
        self.out_amt = tx_amt[order]
        self.out_bal = tx_bal[order]
        self.out_sess = tx_sess[order]
        self.out_xfer = tx_xfer[order]
        in_ok = to_acc >= 0
        in_order = np.lexsort((tx_ts[in_ok], to_acc[in_ok]))
        self.in_acc = to_acc[in_ok][in_order]
        self.in_ts = tx_ts[in_ok][in_order]
        self.in_from = from_acc[in_ok][in_order]
        self.in_amt = tx_amt[in_ok][in_order]

        events = frames["events"]
        self.ev_sess = np.array([session_code.get(item, -1) for item in _text(events["session_id"])], dtype=np.int32)
        self.ev_ts = _ns(events["ts"])
        self.ev_type = _text(events["event_type"])
        order = np.lexsort((self.ev_ts, self.ev_sess))
        self.ev_sess, self.ev_ts, self.ev_type = self.ev_sess[order], self.ev_ts[order], self.ev_type[order]

        calls = frames["calls"]
        call_sess = np.array([session_code.get(item, -1) for item in _text(calls["session_id"])], dtype=np.int32)
        self.call_ts = _ns(calls["ts"])
        self.call_dur = calls["duration_s"].fillna(0).to_numpy(dtype=np.int64) * NS
        phones = frames["phones"].reset_index(drop=True)
        self.number_id = _text(phones["number_id"])
        number_code = {item: index for index, item in enumerate(self.number_id)}
        self.registered = _ns(phones["registered_at"])
        self.reported_count = phones["reported_count"].fillna(0).to_numpy(dtype=np.int64)
        self.call_num = np.array([number_code.get(item, -1) for item in _text(calls["counterparty_number_id"])], dtype=np.int32)
        self.call_cust = np.array([self.sess_cust[item] if item >= 0 else -1 for item in call_sess], dtype=np.int32)
        order = np.lexsort((self.call_ts, self.call_cust))
        self.call_cust, self.call_ts = self.call_cust[order], self.call_ts[order]
        self.call_dur, self.call_num = self.call_dur[order], self.call_num[order]

        payees = frames["payees"]
        self.pay_from = np.array([account_code.get(item, -1) for item in _text(payees["from_account"])], dtype=np.int32)
        self.pay_to = np.array([account_code.get(item, -1) for item in _text(payees["to_account"])], dtype=np.int32)
        self.pay_added = _ns(payees["added_at"])
        self.pay_method = _text(payees["add_method"])
        order = np.lexsort((self.pay_added, self.pay_from))
        self.pay_from, self.pay_to = self.pay_from[order], self.pay_to[order]
        self.pay_added, self.pay_method = self.pay_added[order], self.pay_method[order]

        simo = frames["simo"]
        self.simo_acc = np.array([account_code.get(item, -1) for item in _text(simo["account_id"])], dtype=np.int32)
        self.simo_listed_ns = _ns(simo["listed_at"])
        order = np.lexsort((self.simo_listed_ns, self.simo_acc))
        self.simo_acc, self.simo_listed_ns = self.simo_acc[order], self.simo_listed_ns[order]

        reports = frames["number_reports"]
        self.report_num = np.array([number_code.get(item, -1) for item in _text(reports["number_id"])], dtype=np.int32)
        self.report_ts = _ns(reports["reported_at"])
        order = np.lexsort((self.report_ts, self.report_num))
        self.report_num, self.report_ts = self.report_num[order], self.report_ts[order]

        links = frames["links"]
        self.link_acc = np.array([account_code.get(item, -1) for item in _text(links["account_id"])], dtype=np.int32)
        self.link_dev = np.array([device_code.get(item, -1) for item in _text(links["device_id"])], dtype=np.int32)
        self.link_first = _ns(links["first_seen"])
        order = np.lexsort((self.link_first, self.link_dev))
        self.link_dev, self.link_acc, self.link_first = self.link_dev[order], self.link_acc[order], self.link_first[order]

        signals = frames["signals"]
        self.sig_sess = np.array([session_code.get(item, -1) for item in _text(signals["session_id"])], dtype=np.int32)

        def _flag(name: str) -> np.ndarray:
            if name not in signals.columns:
                return np.zeros(len(signals), dtype=bool)
            return signals[name].fillna(False).to_numpy(dtype=bool)

        audio_col = "in_call_audio_mode" if "in_call_audio_mode" in signals.columns else "in_call_at_open"
        self.sig_remote = _flag("remote_access_app_installed")
        if "accessibility_enabled_since" in signals.columns:
            self.sig_access = _ns(signals["accessibility_enabled_since"])
        else:
            self.sig_access = np.full(len(signals), np.iinfo(np.int64).min, dtype=np.int64)
        self.sig_share = _flag("screen_sharing")
        self.sig_overlay = _flag("overlay_detected")
        self.sig_audio = _flag(audio_col)
        self.sig_talk = _flag("talkback_enabled")
        order = np.argsort(self.sig_sess, kind="mergesort")
        for name in ("sig_sess", "sig_remote", "sig_access", "sig_share", "sig_overlay", "sig_audio", "sig_talk"):
            setattr(self, name, getattr(self, name)[order])
        self._bounds()

    def _bounds(self) -> None:
        self.out_bound = _bounds(self.out_acc)
        self.in_bound = _bounds(self.in_acc)
        self.ev_bound = _bounds(self.ev_sess)
        self.call_bound = _bounds(self.call_cust)
        self.pay_bound = _bounds(self.pay_from)
        self.simo_bound = _bounds(self.simo_acc)
        self.report_bound = _bounds(self.report_num)
        self.link_bound = _bounds(self.link_dev)
        self.sig_bound = _bounds(self.sig_sess)
        order = np.lexsort((self.link_first, self.link_acc))
        self.acc_link_acc = self.link_acc[order]
        self.acc_link_dev = self.link_dev[order]
        self.acc_link_first = self.link_first[order]
        self.acc_link_bound = _bounds(self.acc_link_acc)

    def compute(self, session_id: str, as_of_ts, view: str, profile: str = "advanced") -> dict:
        if profile not in PROFILES:
            raise ValueError("profile phải là basic, standard hoặc advanced")
        allowed = PROFILES[profile]
        as_of = _as_ns(as_of_ts)
        code = self.session_of.get(session_id)
        row = {name: None for name in FEATURE_COLUMNS}
        for name in ("has_tx_signals", "has_payee_signals", "has_device_signals", "has_telco_signals", "pasted", "new_payee", "simo_listed"):
            row[name] = 0
        if code is None or self.sess_start[code] >= as_of:
            return apply_profile(row, profile, view)
        account = int(self.sess_acc[code])
        customer = int(self.sess_cust[code])
        network = view == "network"
        sources = self.accounts_of.get(customer, np.array([account], dtype=np.int32)) if network else np.array([account], dtype=np.int32)
        history = self._history(sources, as_of)
        decision = self._decision(history, code, as_of)
        if decision is None:
            row["has_tx_signals"] = 0
        else:
            row["has_tx_signals"] = 1
            amount = int(decision["amount"])
            balance = int(decision["balance"])
            payee = int(decision["payee"])
            when = int(decision["ts"])
            row["amount"] = amount
            row["amount_to_balance"] = amount / balance if balance > 0 else None
            row["hour"] = int(((when // NS) + TZ_OFFSET_S) % 86400 // 3600)
            row["new_payee"] = int(decision["new"])
            row["a_w"] = self._window_amount(history, as_of, when)
            row["new_payees_24h"] = self._new_payee_count(history, as_of)
            row["pasted"] = self._flag_event(code, as_of, "paste_account")
            row["add_method"] = self._add_method(account, payee, as_of)
            login = self._event_ts(code, as_of, "login")
            row["seconds_since_login"] = (when - login) / NS if login is not None and when >= login else None
            self._payee(row, account, payee, as_of, network and "payee_account_age_days" in allowed)
        row.update(self._behavior(history, as_of, int(decision["payee"]) if decision is not None else -1))
        if "has_device_signals" in allowed:
            self._device(row, code, as_of)
        if "has_telco_signals" in allowed:
            self._telco(row, customer, as_of)
        if not network:
            for name in NETWORK_ONLY:
                row[name] = None
        return apply_profile(row, profile, view)

    def behavior_at(self, session_id: str, as_of_ts, view: str) -> dict:
        """Năm đặc trưng dài hạn tại as_of_ts. Cùng công thức với compute."""
        as_of = _as_ns(as_of_ts)
        code = self.session_of.get(str(session_id))
        if code is None or self.sess_start[code] >= as_of:
            return {name: None for name in LONG_TERM}
        account = int(self.sess_acc[code])
        customer = int(self.sess_cust[code])
        sources = self.accounts_of.get(customer, np.array([account], dtype=np.int32)) if view == "network" else np.array([account], dtype=np.int32)
        history = self._history(sources, as_of)
        decision = self._decision(history, code, as_of)
        payee = int(decision["payee"]) if decision is not None else -1
        return self._behavior(history, as_of, payee)

    def _span(self, bound: dict[int, tuple[int, int]], key: int) -> tuple[int, int]:
        return bound.get(int(key), (0, 0))

    def _history(self, accounts: np.ndarray, as_of: int) -> dict[str, np.ndarray]:
        parts = []
        for account in accounts:
            lo, hi = self._span(self.out_bound, int(account))
            if lo == hi:
                continue
            stop = lo + int(np.searchsorted(self.out_ts[lo:hi], as_of, side="left"))
            if stop > lo:
                parts.append((int(account), lo, stop))
        if not parts:
            empty = np.array([], dtype=np.int64)
            return {"ts": empty, "to": empty.astype(np.int32), "amt": empty, "bal": empty, "sess": empty.astype(np.int32), "xfer": empty.astype(bool), "acc": empty.astype(np.int32)}
        ts = np.concatenate([self.out_ts[lo:hi] for _, lo, hi in parts])
        order = np.argsort(ts, kind="mergesort")
        return {
            "ts": ts[order],
            "to": np.concatenate([self.out_to[lo:hi] for _, lo, hi in parts])[order],
            "amt": np.concatenate([self.out_amt[lo:hi] for _, lo, hi in parts])[order],
            "bal": np.concatenate([self.out_bal[lo:hi] for _, lo, hi in parts])[order],
            "sess": np.concatenate([self.out_sess[lo:hi] for _, lo, hi in parts])[order],
            "xfer": np.concatenate([self.out_xfer[lo:hi] for _, lo, hi in parts])[order],
            "acc": np.concatenate([self.out_acc[lo:hi] for _, lo, hi in parts])[order],
        }

    def _decision(self, history: dict[str, np.ndarray], session: int, as_of: int) -> dict | None:
        if len(history["ts"]) == 0:
            return None
        own = np.flatnonzero((history["sess"] == session) & history["xfer"] & (history["to"] >= 0) & (history["ts"] < as_of))
        if len(own) == 0:
            return None
        index = int(own[-1])
        payee = int(history["to"][index])
        earlier = history["to"][:index]
        return {
            "index": index,
            "ts": int(history["ts"][index]),
            "amount": int(history["amt"][index]),
            "balance": int(history["bal"][index]),
            "payee": payee,
            "new": bool(payee not in earlier),
        }

    def _behavior(self, history: dict[str, np.ndarray], as_of: int, payee: int) -> dict:
        """Hành vi dài hạn chỉ từ giao dịch có ts < as_of."""
        ts = history["ts"]
        dest = history["to"]
        amt = history["amt"]
        bal = history["bal"]
        xfer = history["xfer"]
        sent = xfer & (dest >= 0) & (ts < as_of)
        start_30 = as_of - 30 * DAY_NS
        start_7 = as_of - 7 * DAY_NS
        if not np.any(sent):
            return {
                "cusum_out_30d": 0.0,
                "new_payees_7d": 0,
                "new_payees_30d": 0,
                "amount_rise_run": 0,
                "balance_out_ratio_30d": 0.0,
            }
        order = np.argsort(ts[sent], kind="mergesort")
        sent_ts = ts[sent][order]
        sent_to = dest[sent][order]
        sent_amt = amt[sent][order]
        sent_bal = bal[sent][order]
        prior = sent_ts <= start_30
        mu = float(sent_amt[prior].mean()) if np.any(prior) else 0.0
        window = sent_amt[sent_ts > start_30]
        state = 0.0
        for value in window:
            state = max(0.0, state + float(value) - mu)
        _, first_at = np.unique(sent_to, return_index=True)
        first_ts = sent_ts[first_at]
        fresh_30 = int(np.sum(first_ts > start_30))
        fresh_7 = int(np.sum(first_ts > start_7))
        run = 0
        if payee >= 0:
            chain = sent_amt[sent_to == payee]
            run = 1 if len(chain) else 0
            for older, newer in zip(chain[-2::-1], chain[::-1]):
                if float(newer) > float(older):
                    run += 1
                else:
                    break
        in_month = sent_ts > start_30
        ratio = 0.0
        if np.any(in_month):
            spent = float(sent_amt[in_month].sum())
            base = float(sent_bal[in_month][0])
            ratio = None if base <= 0 else spent / base
        return {
            "cusum_out_30d": state,
            "new_payees_7d": fresh_7,
            "new_payees_30d": fresh_30,
            "amount_rise_run": run,
            "balance_out_ratio_30d": ratio,
        }

    def _window_amount(self, history: dict[str, np.ndarray], as_of: int, _when: int) -> int:
        start = as_of - DAY_NS
        total = 0
        seen: set[int] = set()
        for ts, dest, amt, xfer in zip(history["ts"], history["to"], history["amt"], history["xfer"]):
            if dest >= 0:
                first = dest not in seen
                seen.add(int(dest))
            else:
                first = False
            if ts <= start or not xfer or dest < 0:
                continue
            if first or self._simo(int(dest), int(ts)):
                total += int(amt)
        return total

    def _new_payee_count(self, history: dict[str, np.ndarray], as_of: int) -> int:
        start = as_of - DAY_NS
        seen: set[int] = set()
        count = 0
        for ts, dest, xfer in zip(history["ts"], history["to"], history["xfer"]):
            if dest < 0:
                continue
            first = dest not in seen
            seen.add(int(dest))
            if first and xfer and ts > start:
                count += 1
        return count

    def _simo(self, account: int, as_of: int) -> bool:
        lo, hi = self._span(self.simo_bound, account)
        if lo == hi:
            return False
        return bool(np.searchsorted(self.simo_listed_ns[lo:hi], as_of, side="left") > 0)

    def _add_method(self, account: int, payee: int, as_of: int) -> str | None:
        lo, hi = self._span(self.pay_bound, account)
        if lo == hi:
            return None
        added = self.pay_added[lo:hi]
        stop = lo + int(np.searchsorted(added, as_of, side="left"))
        method = None
        for dest, name in zip(self.pay_to[lo:stop], self.pay_method[lo:stop]):
            if int(dest) == payee:
                method = str(name)
        return method

    def _event_slice(self, session: int, as_of: int) -> tuple[np.ndarray, np.ndarray]:
        lo, hi = self._span(self.ev_bound, session)
        if lo == hi:
            return np.array([], dtype=np.int64), np.array([], dtype=object)
        stop = lo + int(np.searchsorted(self.ev_ts[lo:hi], as_of, side="left"))
        return self.ev_ts[lo:stop], self.ev_type[lo:stop]

    def _flag_event(self, session: int, as_of: int, name: str) -> int:
        _, kinds = self._event_slice(session, as_of)
        return int(name in set(kinds.tolist()))

    def _event_ts(self, session: int, as_of: int, name: str) -> int | None:
        stamps, kinds = self._event_slice(session, as_of)
        picked = stamps[kinds == name] if len(stamps) else np.array([], dtype=np.int64)
        return int(picked[0]) if len(picked) else None

    def _payee(self, row: dict, sender: int, payee: int, as_of: int, network: bool) -> None:
        if payee < 0:
            return
        row["has_payee_signals"] = 1
        row["simo_listed"] = int(self._simo(payee, as_of))
        opened = int(self.opened[payee])
        visible = _known(opened) and opened < as_of
        sender_name = str(self.holder[sender]) if sender >= 0 else ""
        payee_name = str(self.holder[payee]) if visible else ""
        if sender_name and payee_name:
            row["payee_name_matches_holder"] = int(sender_name == payee_name)
        if visible and (network or bool(self.bank_b01[payee])):
            row["merchant_verified"] = int(bool(self.merchant[payee]))
        if not network or not visible:
            return
        row["payee_account_age_days"] = (as_of - opened) / DAY_NS
        row["distinct_senders_7d"] = self._senders(payee, as_of)
        row["passthrough_24h"] = self._passthrough(payee, as_of)
        row["device_holder_count"] = self._holders(payee, as_of)

    def _senders(self, payee: int, as_of: int) -> int:
        lo, hi = self._span(self.in_bound, payee)
        if lo == hi:
            return 0
        start = as_of - 7 * DAY_NS
        ts = self.in_ts[lo:hi]
        left = int(np.searchsorted(ts, start, side="right"))
        right = int(np.searchsorted(ts, as_of, side="left"))
        if right <= left:
            return 0
        return int(len(np.unique(self.in_from[lo + left : lo + right])))

    def _passthrough(self, payee: int, as_of: int) -> float | None:
        lo, hi = self._span(self.in_bound, payee)
        if lo == hi:
            return None
        ts = self.in_ts[lo:hi]
        left = int(np.searchsorted(ts, as_of - 2 * DAY_NS, side="right"))
        right = int(np.searchsorted(ts, as_of - DAY_NS, side="right"))
        if right <= left:
            return None
        out_lo, out_hi = self._span(self.out_bound, payee)
        ratios = []
        for offset in range(left, right):
            amount = int(self.in_amt[lo + offset])
            if amount <= 0:
                continue
            stamp = int(ts[offset])
            if out_lo == out_hi:
                ratios.append(0.0)
                continue
            out_ts = self.out_ts[out_lo:out_hi]
            begin = int(np.searchsorted(out_ts, stamp, side="right"))
            end = int(np.searchsorted(out_ts, min(stamp + DAY_NS, as_of), side="right"))
            moved = int(self.out_amt[out_lo + begin : out_lo + end][self.out_xfer[out_lo + begin : out_lo + end]].sum()) if end > begin else 0
            ratios.append(min(1.0, moved / amount))
        return float(np.mean(ratios)) if ratios else None

    def _holders(self, payee: int, as_of: int) -> int | None:
        lo, hi = self._span(self.acc_link_bound, payee)
        if lo == hi:
            return None
        first = self.acc_link_first[lo:hi]
        stop = int(np.searchsorted(first, as_of, side="left"))
        if stop == 0:
            return None
        device = int(self.acc_link_dev[lo + stop - 1])
        dev_lo, dev_hi = self._span(self.link_bound, device)
        if dev_lo == dev_hi:
            return None
        seen = self.link_first[dev_lo:dev_hi]
        end = int(np.searchsorted(seen, as_of, side="left"))
        accounts = self.link_acc[dev_lo : dev_lo + end]
        customers = {int(self.customer_of[item]) for item in accounts if item >= 0 and int(self.customer_of[item]) >= 0}
        return len(customers)

    def _device(self, row: dict, session: int, as_of: int) -> None:
        lo, hi = self._span(self.sig_bound, session)
        if lo == hi or self.sess_start[session] >= as_of:
            return
        row["has_device_signals"] = 1
        row["in_call_audio_mode"] = int(bool(self.sig_audio[lo]))
        row["remote_access_app"] = int(bool(self.sig_remote[lo]))
        row["screen_sharing"] = int(bool(self.sig_share[lo]))
        row["overlay"] = int(bool(self.sig_overlay[lo]))
        enabled = int(self.sig_access[lo])
        row["accessibility_new"] = int(_known(enabled) and enabled < as_of and as_of - enabled < DAY_NS)
        device = int(self.sess_dev[session])
        if device >= 0 and _known(int(self.device_first[device])) and int(self.device_first[device]) < as_of:
            row["new_device"] = int(as_of - int(self.device_first[device]) < DAY_NS)
        else:
            row["new_device"] = 0

    def _telco(self, row: dict, customer: int, as_of: int) -> None:
        lo, hi = self._span(self.call_bound, customer)
        if lo == hi or customer < 0:
            return
        ts = self.call_ts[lo:hi]
        stop = lo + int(np.searchsorted(ts, as_of, side="left"))
        if stop == lo:
            return
        row["has_telco_signals"] = 1
        latest = stop - 1
        start = int(self.call_ts[latest])
        dur = int(self.call_dur[latest])
        row["on_call"] = int(start + dur >= as_of)
        row["recent_call_minutes_ago"] = 0.0 if start + dur >= as_of else (as_of - start) / (60 * NS)
        number = int(self.call_num[latest])
        row["counterparty_is_new"] = self._number_is_new(customer, number, start, as_of)
        if number >= 0 and _known(int(self.registered[number])) and int(self.registered[number]) < as_of:
            row["counterparty_number_age_days"] = (as_of - int(self.registered[number])) / DAY_NS
            row["counterparty_report_count"] = int(self.reported_count[number]) + self._reports(number, as_of)
        else:
            row["counterparty_number_age_days"] = None
            row["counterparty_report_count"] = None

    def _number_is_new(self, customer: int, number: int, call_ts: int, as_of: int) -> int:
        if self.contacts is not None:
            customer_name = str(self.customer_id[customer]) if customer >= 0 else ""
            number_name = str(self.number_id[number]) if number >= 0 else ""
            first = self.contacts.get((customer_name, number_name))
            return int(first is None or first >= as_of)
        lo, hi = self._span(self.call_bound, customer)
        prior = self.call_num[lo:hi]
        prior_ts = self.call_ts[lo:hi]
        return int(not np.any((prior == number) & (prior_ts < call_ts)))

    def _reports(self, number: int, as_of: int) -> int:
        lo, hi = self._span(self.report_bound, number)
        if lo == hi:
            return 0
        return int(np.searchsorted(self.report_ts[lo:hi], as_of, side="left"))


def _bounds(codes: np.ndarray) -> dict[int, tuple[int, int]]:
    if len(codes) == 0:
        return {}
    change = np.flatnonzero(np.diff(codes)) + 1
    starts = np.r_[0, change]
    ends = np.r_[change, len(codes)]
    return {int(codes[start]): (int(start), int(end)) for start, end in zip(starts, ends) if codes[start] >= 0}


def unread_signals(profile: str, view: str) -> list[str]:
    """Tên tín hiệu profile này không được đọc. View một ngân hàng cũng không đọc sổ mạng lưới."""
    if profile not in PROFILES:
        raise ValueError("profile phải là basic, standard hoặc advanced")
    allowed = PROFILES[profile]
    network = view == "network"
    return [name for name in FEATURE_COLUMNS if name not in allowed or (name in NETWORK_ONLY and not network)]


def apply_profile(row: dict, profile: str, view: str) -> dict:
    missing = unread_signals(profile, view)
    for name in missing:
        row[name] = None
    row["missing_signals"] = missing
    for name in NETWORK_ONLY:
        # Thiếu là NaN và cờ 1. Giá trị 0 chỉ khi đã quan sát được và đếm ra không.
        absent = row.get(name) is None
        row[f"{name}_missing"] = int(absent)
    return row


def _known(stamp: int) -> bool:
    return 0 < stamp < np.iinfo(np.int64).max


def _as_ns(value) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Ho_Chi_Minh")
    return int(stamp.tz_convert("UTC").value)
