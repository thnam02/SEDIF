"""Phiên test sau 20/9 được chấm từ snapshot, không phát lại từ đầu tháng 7."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from engine.common.hashing import reset_key_cache
from engine.decide import assign_level, gated_families
from engine.features.online import OnlineStore
from engine.features.stream import IdCache, timed_events, to_us
from engine.model import Scorer
from eval.model_v21 import demo_frame
from features.service import session_families
from scripts.replay import SIM_END, _until_us

OBSERVED = REPO / "fides-sim" / "out" / "observed"
SINCE = "2026-09-20T00:00:00+07:00"
PARQUET_COLUMNS = [
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
    "pasted",
    "has_device_signals",
    "payee_account_age_days",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
    "simo_listed",
]
COUNT_COLUMNS = (
    "product_savings_7d",
    "product_loan_7d",
    "product_cash_advance_7d",
    "limit_raise_1d",
    "phone_change_7d",
)
MISSING = {
    "payee_account_age_days": "payee_account_age_days_missing",
    "distinct_senders_7d": "distinct_senders_7d_missing",
    "passthrough_24h": "passthrough_24h_missing",
    "device_holder_count": "device_holder_count_missing",
}


def _same(left, right) -> bool:
    if left is None or (isinstance(left, float) and np.isnan(left)):
        return right is None or (isinstance(right, float) and np.isnan(right)) or pd.isna(right)
    if right is None or pd.isna(right):
        return False
    if isinstance(left, str) or isinstance(right, str):
        return str(left) == str(right)
    return abs(float(left) - float(right)) <= 1e-6


def test_sessions_after_snapshot_match_offline():
    reset_key_cache()
    store = OnlineStore(flush=False)
    store.index_customers()
    marker = store.redis.get("f:snapshot_until")
    assert marker == SINCE
    sessions = pd.read_parquet(OBSERVED / "sessions.parquet", columns=["session_id", "account_id", "start_ts", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    start = pd.to_datetime(sessions["start_ts"])
    chosen = sessions.loc[(start >= SINCE) & (start < "2026-09-29")].reset_index(drop=True)
    cache = IdCache(OBSERVED)
    store.session_account = {row.session_id: cache.get(row.account_id) for row in sessions.itertuples(index=False) if cache.get(row.account_id)}
    since_us = _until_us(SINCE)
    end_us = to_us(chosen["end_ts"])
    pending = []
    for index, row in enumerate(chosen.itertuples(index=False)):
        sender = store.session_account.get(row.session_id)
        if sender:
            pending.append((int(end_us[index]) + 1, row.session_id, sender))
    pending.sort()
    cursor = 0
    seen = 0
    results = {}
    continued = store.redis.get("f:continued_until") == "2026-09-29T00:00:00+07:00"
    if os.environ.get("FIDES_PARITY_COMPARE") == "1" or continued:
        for as_of, session_id, sender in pending:
            payee = store.redis.hget(f"f:dec:{session_id}", "payee")
            results[session_id] = store.get(sender, payee, as_of, session_id)
        print(f"compare sessions {len(results)}", flush=True)
    else:
        for _stamp, _rank, _index, event in timed_events(OBSERVED, cache, _until_us(SIM_END)):
            if int(event["ts"]) < since_us:
                continue
            while cursor < len(pending) and pending[cursor][0] <= int(event["ts"]):
                as_of, session_id, sender = pending[cursor]
                payee = store.redis.hget(f"f:dec:{session_id}", "payee")
                results[session_id] = store.get(sender, payee, as_of, session_id)
                cursor += 1
            store.update(event)
            seen += 1
            if seen % 200_000 == 0:
                print(f"  continue {seen}", flush=True)
        store.flush()
        while cursor < len(pending):
            as_of, session_id, sender = pending[cursor]
            payee = store.redis.hget(f"f:dec:{session_id}", "payee")
            results[session_id] = store.get(sender, payee, as_of, session_id)
            cursor += 1
        print(f"continued events {seen} sessions {len(results)}", flush=True)
        store.redis.set("f:continued_until", "2026-09-29T00:00:00+07:00")
    assert len(results) == len(chosen)
    families = session_families(OBSERVED).set_index("session_id")
    offline = pd.read_parquet(REPO / "fides" / "out" / "features_network_test.parquet")
    offline["session_id"] = offline["session_id"].astype(str)
    offline = offline.loc[offline["session_id"].isin(set(chosen["session_id"]))].set_index("session_id")
    counted = demo_frame({"after": chosen[["session_id", "end_ts"]]}).set_index("session_id")
    customers = pd.read_parquet(OBSERVED / "customers.parquet", columns=["customer_id", "segment", "has_guardian"])
    customers["customer_id"] = customers["customer_id"].astype(str)
    customers = customers.drop_duplicates("customer_id").set_index("customer_id")
    holders = pd.read_parquet(OBSERVED / "accounts.parquet", columns=["account_id", "customer_id"])
    holders["account_id"] = holders["account_id"].astype(str)
    holders["customer_id"] = holders["customer_id"].astype(str)
    holders = holders.drop_duplicates("account_id").set_index("account_id")["customer_id"]
    owners = dict(zip(chosen["session_id"], chosen["account_id"]))
    scorer = Scorer.load()
    bad_columns = []
    bad_levels = []
    bad_families = []
    for session_id, row in results.items():
        expected = families.loc[session_id]
        got = (int(row["call_family"]), int(row["entry_family"]), int(row["payee_family"]), int(row["funding_family"]))
        want = (int(bool(expected["call"])), int(bool(expected["entry"])), int(bool(expected["payee"])), int(bool(expected["funding"])))
        if got != want and len(bad_families) < 5:
            bad_families.append((session_id, got, want))
        base = offline.loc[session_id]
        extra = counted.loc[session_id]
        person = customers.loc[holders.loc[owners[session_id]]]
        for name in (*PARQUET_COLUMNS, *COUNT_COLUMNS):
            source = extra[name] if name in COUNT_COLUMNS else base[name]
            if not _same(row.get(name), source):
                if len(bad_columns) < 8:
                    bad_columns.append((session_id, name, row.get(name), source))
                break
        if str(row.get("segment") or "") != str(person["segment"]) or int(bool(row.get("has_guardian"))) != int(bool(person["has_guardian"])):
            if len(bad_columns) < 8:
                bad_columns.append((session_id, "segment", row.get("segment"), person["segment"]))
        level = assign_level(
            scorer.score(row),
            0.0 if row.get("amount") is None else float(row["amount"]),
            gated_families(row),
            str(row.get("segment") or "thường"),
            row,
        )
        twin = {name: (None if pd.isna(base[name]) else base[name]) for name in PARQUET_COLUMNS}
        for name in COUNT_COLUMNS:
            twin[name] = int(extra[name])
        for source, flag in MISSING.items():
            value = twin.get(source)
            twin[flag] = 1 if value is None or pd.isna(value) else 0
        twin["call_family"] = int(bool(expected["call"]))
        twin["entry_family"] = int(bool(expected["entry"]))
        twin["payee_family"] = int(bool(expected["payee"]))
        twin["funding_family"] = int(bool(expected["funding"]))
        twin["segment"] = str(person["segment"])
        twin["has_guardian"] = bool(person["has_guardian"])
        twin["in_call"] = 0 if pd.isna(extra["in_call"]) else int(extra["in_call"])
        other = assign_level(
            scorer.score(twin),
            0.0 if twin.get("amount") is None else float(twin["amount"]),
            gated_families(twin),
            str(twin.get("segment") or "thường"),
            twin,
        )
        if level != other and len(bad_levels) < 5:
            bad_levels.append((session_id, level, other))
    print("family", bad_families, "columns", bad_columns, "levels", bad_levels, flush=True)
    assert bad_families == []
    assert bad_columns == []
    assert bad_levels == []
