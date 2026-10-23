"""Dòng sự kiện point-in-time từ observed/. Không đọc truth/."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from engine.common.hashing import account_hash

TRANSFER_TYPES = {
    "transfer",
    "rent",
    "school_fee",
    "large_oneoff",
    "own_transfer",
    "own_transfer_drain",
    "passthrough_out",
}
ACCT_INPUT = {"typed", "typed_chunked", "pasted", "chosen"}


def to_us(series: pd.Series) -> np.ndarray:
    values = pd.to_datetime(series, utc=True)
    out = np.full(len(series), np.iinfo(np.int64).max, dtype=np.int64)
    ok = values.notna().to_numpy()
    if ok.any():
        stamps = values[ok]
        if getattr(stamps.dtype, "unit", "ns") != "ns":
            stamps = stamps.dt.as_unit("ns")
        out[ok] = stamps.astype("int64").to_numpy() // 1000
    return out


def _text(series: pd.Series) -> np.ndarray:
    values = series.astype("string").fillna("").to_numpy()
    return np.array(["" if item in {"<NA>", "nan", "None"} else str(item) for item in values], dtype=object)


def _flag(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return ""
    return "1" if bool(value) else "0"


class IdCache:
    def __init__(self, observed: Path):
        accounts = pd.read_parquet(observed / "accounts.parquet", columns=["account_id"])
        transactions = pd.read_parquet(observed / "transactions.parquet", columns=["from_account"])
        self.known = {item for item in _text(accounts["account_id"]) if item}
        self._hash = {item: account_hash(item) for item in self.known}
        for item in _text(transactions["from_account"]):
            if item and item not in self._hash:
                self._hash[item] = account_hash(item)

    def get(self, raw: str) -> str | None:
        if not raw:
            return None
        return self._hash.get(raw)


def account_events(observed: Path, cache: IdCache):
    frame = pd.read_parquet(
        observed / "accounts.parquet",
        columns=["account_id", "customer_id", "bank_id", "holder_name_ascii", "opened_at", "merchant_verified"],
    )
    opened = to_us(frame["opened_at"])
    account = _text(frame["account_id"])
    customer = _text(frame["customer_id"])
    holder = _text(frame["holder_name_ascii"])
    bank = _text(frame["bank_id"])
    merchant = frame["merchant_verified"].fillna(False).to_numpy(dtype=bool)
    for index, raw in enumerate(account):
        if not raw:
            continue
        yield {
            "type": "account",
            "account": cache.get(raw),
            "opened": int(opened[index]) if opened[index] < np.iinfo(np.int64).max else 0,
            "holder": str(holder[index]),
            "merchant": int(bool(merchant[index])),
            "b01": int(bank[index] == "B01"),
            "customer": str(customer[index]),
        }


def timed_events(observed: Path, cache: IdCache, until_us: int):
    """Mọi sự kiện có mốc < until, đã xếp theo thời gian."""
    streams = [
        _links(observed, cache, until_us),
        _payees(observed, cache, until_us),
        _simo(observed, cache, until_us),
        _transactions(observed, cache, until_us),
        _changes(observed, cache, until_us),
        _session_events(observed, until_us),
        _signals(observed, until_us),
    ]
    import heapq

    yield from heapq.merge(*streams)


def _ordered(ts: np.ndarray, until_us: int):
    order = np.lexsort((np.arange(len(ts)), ts))
    for index in order:
        stamp = int(ts[index])
        if stamp >= until_us:
            break
        if stamp == np.iinfo(np.int64).max:
            break
        yield int(index), stamp


def _links(observed: Path, cache: IdCache, until_us: int):
    frame = pd.read_parquet(observed / "account_device.parquet", columns=["account_id", "device_id", "first_seen"])
    ts = to_us(frame["first_seen"])
    account = _text(frame["account_id"])
    device = _text(frame["device_id"])
    for index, stamp in _ordered(ts, until_us):
        hashed = cache.get(str(account[index]))
        if not hashed or not device[index]:
            continue
        yield stamp, 1, index, {
            "type": "device_link",
            "ts": stamp,
            "account": hashed,
            "device": str(device[index]),
        }


def _payees(observed: Path, cache: IdCache, until_us: int):
    frame = pd.read_parquet(observed / "payees.parquet", columns=["from_account", "to_account", "added_at", "add_method"])
    ts = to_us(frame["added_at"])
    origin = _text(frame["from_account"])
    dest = _text(frame["to_account"])
    method = _text(frame["add_method"])
    for index, stamp in _ordered(ts, until_us):
        sender = cache.get(str(origin[index]))
        payee = cache.get(str(dest[index])) if str(dest[index]) in cache.known else None
        if not sender or not payee:
            continue
        yield stamp, 2, index, {
            "type": "payee_add",
            "ts": stamp,
            "account": sender,
            "payee": payee,
            "method": str(method[index]),
        }


def _simo(observed: Path, cache: IdCache, until_us: int):
    frame = pd.read_parquet(observed / "simo_list.parquet", columns=["account_id", "listed_at"])
    ts = to_us(frame["listed_at"])
    account = _text(frame["account_id"])
    for index, stamp in _ordered(ts, until_us):
        hashed = cache.get(str(account[index]))
        if not hashed:
            continue
        yield stamp, 3, index, {"type": "simo_listing", "ts": stamp, "account": hashed}


def _transactions(observed: Path, cache: IdCache, until_us: int):
    frame = pd.read_parquet(
        observed / "transactions.parquet",
        columns=["ts", "from_account", "to_account", "amount", "tx_type", "session_id", "from_balance_before"],
    )
    ts = to_us(frame["ts"])
    origin = _text(frame["from_account"])
    dest = _text(frame["to_account"])
    session = _text(frame["session_id"])
    kind = _text(frame["tx_type"])
    amount = frame["amount"].fillna(0).to_numpy(dtype=np.int64)
    balance = frame["from_balance_before"].fillna(0).to_numpy(dtype=np.int64)
    for index, stamp in _ordered(ts, until_us):
        sender = cache.get(str(origin[index]))
        if not sender:
            continue
        raw_dest = str(dest[index])
        payee = cache.get(raw_dest) if raw_dest in cache.known else None
        tx_type = str(kind[index])
        yield stamp, 4, index, {
            "type": "txn",
            "ts": stamp,
            "account": sender,
            "payee": payee,
            "amount": int(amount[index]),
            "balance": int(balance[index]),
            "session": str(session[index]),
            "tx_type": tx_type,
            "xfer": tx_type in TRANSFER_TYPES,
        }


def _changes(observed: Path, cache: IdCache, until_us: int):
    frame = pd.read_parquet(observed / "account_changes.parquet", columns=["account_id", "ts", "change_type"])
    ts = to_us(frame["ts"])
    account = _text(frame["account_id"])
    kind = _text(frame["change_type"])
    for index, stamp in _ordered(ts, until_us):
        name = str(kind[index])
        if name not in {"limit_raise", "phone_change"}:
            continue
        hashed = cache.get(str(account[index]))
        if not hashed:
            continue
        yield stamp, 6, index, {
            "type": "account_change",
            "ts": stamp,
            "account": hashed,
            "change_type": name,
        }


def _session_events(observed: Path, until_us: int):
    frame = pd.read_parquet(observed / "session_events.parquet", columns=["session_id", "ts", "event_type"])
    ts = to_us(frame["ts"])
    session = _text(frame["session_id"])
    kind = _text(frame["event_type"])
    for index, stamp in _ordered(ts, until_us):
        yield stamp, 7, index, {
            "type": "session_event",
            "ts": stamp,
            "session": str(session[index]),
            "event_type": str(kind[index]),
        }


def _signals(observed: Path, until_us: int):
    frame = pd.read_parquet(
        observed / "device_signals.parquet",
        columns=["session_id", "in_call_at_open", "call_ended_in_session"],
    )
    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "start_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    frame["session_id"] = frame["session_id"].astype(str)
    frame = frame.drop_duplicates("session_id", keep="first")
    frame = frame.merge(sessions, on="session_id", how="left")
    ts = to_us(frame["start_ts"])
    session = _text(frame["session_id"])
    call = frame["in_call_at_open"].to_numpy()
    ended = frame["call_ended_in_session"].to_numpy()
    for index, stamp in _ordered(ts, until_us):
        yield stamp, 0, index, {
            "type": "device_signal",
            "ts": stamp,
            "session": str(session[index]),
            "in_call": _flag(call[index]),
            "ended": _flag(ended[index]),
        }
