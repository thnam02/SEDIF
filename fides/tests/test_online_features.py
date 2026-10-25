"""Parity đặc trưng online với fides/features/ trên mọi phiên test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from engine.common.hashing import reset_key_cache
from engine.features.online import COLUMNS, FAMILY_COLUMNS
from engine.features.stream import to_us
from features import compute_features
from features.store import DAY_NS, NETWORK_ONLY, FeatureStore, using_store
from scripts.replay import SIM_END, run

OBSERVED = REPO / "fides-sim" / "out" / "observed"
PRODUCT_KIND = {
    "product_savings_7d": "savings_liquidation",
    "product_loan_7d": "quick_loan_disbursement",
    "product_cash_advance_7d": "credit_card_cash_advance",
}
ACCT_INPUT = {"typed", "typed_chunked", "pasted", "chosen"}


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return False
    return bool(pd.isna(value))


def _same(left, right) -> bool:
    if _missing(left) and _missing(right):
        return True
    if _missing(left) or _missing(right):
        return False
    if isinstance(left, str) or isinstance(right, str):
        return str(left) == str(right)
    return abs(float(left) - float(right)) <= 1e-6


def _count_window(keys: np.ndarray, as_of: np.ndarray, event_key: np.ndarray, event_ts: np.ndarray, window_ns: int) -> np.ndarray:
    grouped: dict[str, np.ndarray] = {}
    if len(event_key):
        order = np.argsort(event_key, kind="mergesort")
        sorted_key = event_key[order]
        sorted_ts = event_ts[order]
        change = np.flatnonzero(sorted_key[1:] != sorted_key[:-1]) + 1
        bounds = np.r_[0, change, len(sorted_key)]
        for start, stop in zip(bounds[:-1], bounds[1:]):
            grouped[str(sorted_key[start])] = np.sort(sorted_ts[start:stop])
    out = np.zeros(len(keys), dtype=np.int32)
    for index, key in enumerate(keys):
        stamps = grouped.get(str(key))
        if stamps is None:
            continue
        moment = int(as_of[index])
        right = int(np.searchsorted(stamps, moment, side="left"))
        left = int(np.searchsorted(stamps, moment - window_ns, side="right"))
        out[index] = right - left
    return out


def offline_table(session_ids: list[str]) -> dict[str, dict]:
    reset_key_cache()
    sessions = pd.read_parquet(OBSERVED / "sessions.parquet", columns=["session_id", "account_id", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    wanted = set(session_ids)
    part = sessions.loc[sessions["session_id"].isin(wanted)].copy()
    part["as_of_ns"] = to_us(part["end_ts"]) * 1000 + 1000
    store = FeatureStore(OBSERVED)
    rows: dict[str, dict] = {}
    ends = dict(zip(part["session_id"], part["end_ts"]))
    total = len(part)
    with using_store(store):
        for index, sid in enumerate(part["session_id"], start=1):
            if index == 1 or index % 10000 == 0 or index == total:
                print(f"  offline {index}/{total}", flush=True)
            as_of = pd.Timestamp(ends[sid]) + pd.Timedelta(microseconds=1)
            bank = compute_features(sid, as_of, "single_bank", "advanced")
            network = compute_features(sid, as_of, "network", "advanced")
            for name in NETWORK_ONLY:
                bank[name] = network[name]
                bank[f"{name}_missing"] = network[f"{name}_missing"]
            rows[sid] = bank
    tx = pd.read_parquet(OBSERVED / "transactions.parquet", columns=["from_account", "ts", "tx_type", "session_id"])
    tx["from_account"] = tx["from_account"].astype(str)
    tx["session_id"] = tx["session_id"].astype(str)
    tx["ts_ns"] = to_us(tx["ts"]) * 1000
    kind = tx["tx_type"].astype(str)
    keys = part["account_id"].to_numpy()
    as_of = part["as_of_ns"].to_numpy()
    for column, tx_type in PRODUCT_KIND.items():
        piece = tx.loc[kind.eq(tx_type)]
        counts = _count_window(keys, as_of, piece["from_account"].to_numpy(), piece["ts_ns"].to_numpy(), 7 * DAY_NS)
        for sid, count in zip(part["session_id"], counts):
            rows[sid][column] = int(count)
    changes = pd.read_parquet(OBSERVED / "account_changes.parquet", columns=["account_id", "ts", "change_type"])
    changes["account_id"] = changes["account_id"].astype(str)
    changes["ts_ns"] = to_us(changes["ts"]) * 1000
    change_kind = changes["change_type"].astype(str)
    raises = changes.loc[change_kind.eq("limit_raise")]
    phones = changes.loc[change_kind.eq("phone_change")]
    raise_count = _count_window(keys, as_of, raises["account_id"].to_numpy(), raises["ts_ns"].to_numpy(), DAY_NS)
    phone_count = _count_window(keys, as_of, phones["account_id"].to_numpy(), phones["ts_ns"].to_numpy(), 7 * DAY_NS)
    for sid, raised, phone in zip(part["session_id"], raise_count, phone_count):
        rows[sid]["limit_raise_1d"] = int(raised)
        rows[sid]["phone_change_7d"] = int(phone)
    signals = pd.read_parquet(
        OBSERVED / "device_signals.parquet",
        columns=["session_id", "in_call_at_open", "call_ended_in_session"],
    )
    signals["session_id"] = signals["session_id"].astype(str)
    signals = signals.drop_duplicates("session_id", keep="first").set_index("session_id")
    events = pd.read_parquet(OBSERVED / "session_events.parquet", columns=["session_id", "ts", "event_type"])
    events["session_id"] = events["session_id"].astype(str)
    events["ts_ns"] = to_us(events["ts"]) * 1000
    inputs = events.loc[events["event_type"].astype(str).str.startswith("acct_input_")].copy()
    inputs["acct_input"] = inputs["event_type"].astype(str).str.replace("acct_input_", "", regex=False)
    inputs = inputs.sort_values(["session_id", "ts_ns"], kind="mergesort").groupby("session_id", sort=False).tail(1)
    acct = dict(zip(inputs["session_id"], inputs["acct_input"]))
    as_of_of = dict(zip(part["session_id"], part["as_of_ns"]))
    product_sessions = set()
    piece = tx.loc[kind.isin(set(PRODUCT_KIND.values())), ["session_id", "ts_ns"]]
    for sid, stamp in zip(piece["session_id"], piece["ts_ns"]):
        if int(stamp) < int(as_of_of.get(sid, 0)):
            product_sessions.add(sid)
    for sid in part["session_id"]:
        row = rows[sid]
        if sid in signals.index:
            call = signals.at[sid, "in_call_at_open"]
            ended = signals.at[sid, "call_ended_in_session"]
            row["in_call"] = None if pd.isna(call) else float(bool(call))
            row["call_ended_in_session"] = None if pd.isna(ended) else float(bool(ended))
        else:
            row["in_call"] = None
            row["call_ended_in_session"] = None
        method = acct.get(sid)
        row["acct_input"] = method if method in ACCT_INPUT else None
        row["mobilized"] = int(sid in product_sessions)
    return rows


def _report(online: dict[str, dict], offline: dict[str, dict], stats: dict) -> list[str]:
    print(f"replay_s {stats['replay_s']:.3f}")
    print(f"update_s {stats['update_s']:.3f}")
    print(f"redis_bytes {stats['redis_bytes']}")
    print(f"get_p95_s {stats['get_p95_s']:.6f}")
    print(f"sessions {stats['sessions']}")
    bad: list[str] = []
    for column in (*COLUMNS, *FAMILY_COLUMNS):
        misses = []
        for sid, row in online.items():
            if not _same(row.get(column), offline[sid].get(column)):
                misses.append(sid)
        if not misses:
            continue
        bad.append(column)
        print(f"lệch {column} {len(misses)} phiên")
        for sid in misses[:5]:
            print(f"  {sid} online={online[sid].get(column)!r} offline={offline[sid].get(column)!r}")
    return bad


def test_online_features_match_offline() -> None:
    reset_key_cache()
    bank = pd.read_parquet(ROOT / "out" / "features_single_bank_test.parquet", columns=["session_id"])
    session_ids = bank["session_id"].astype(str).tolist()
    offline = offline_table(session_ids)
    online, stats = run(OBSERVED, set(session_ids), SIM_END, os.environ["REDIS_URL"])
    assert set(online) == set(session_ids)
    mismatches = _report(online, offline, stats)
    assert mismatches == []
