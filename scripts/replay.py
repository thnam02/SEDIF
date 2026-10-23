"""Phát lại observed/ vào feature store theo ts. Tại mỗi phiên test gọi get() đúng lúc quyết định."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.features.online import COLUMNS, FAMILY_COLUMNS, OnlineStore
from engine.features.stream import IdCache, account_events, timed_events, to_us

OBSERVED = ROOT / "fides-sim" / "out" / "observed"
SIM_END = "2026-09-29T00:00:00+07:00"


def _until_us(until: str) -> int:
    stamp = pd.Timestamp(until)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Ho_Chi_Minh")
    return int(stamp.value // 1000)


def run(observed: Path, test_ids: set[str] | None, until: str, redis_url: str | None) -> tuple[dict[str, dict], dict]:
    cache = IdCache(observed)
    until_us = _until_us(until)
    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "account_id", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    store = OnlineStore(redis_url)
    store.session_account = {
        row.session_id: cache.get(row.account_id)
        for row in sessions.itertuples(index=False)
        if cache.get(row.account_id)
    }
    if test_ids is None:
        test_ids = set(sessions["session_id"])
    end_us = to_us(sessions["end_ts"])
    pending = []
    for index, row in enumerate(sessions.itertuples(index=False)):
        if row.session_id not in test_ids:
            continue
        sender = store.session_account.get(row.session_id)
        if not sender:
            continue
        pending.append((int(end_us[index]) + 1, row.session_id, sender))
    pending.sort()
    cursor = 0
    results: dict[str, dict] = {}
    started = time.perf_counter()
    update_seconds = 0.0
    seen = 0
    for event in account_events(observed, cache):
        store.update(event)
    store.flush()
    for _stamp, _rank, _index, event in timed_events(observed, cache, until_us):
        while cursor < len(pending) and pending[cursor][0] <= event["ts"]:
            as_of, session_id, sender = pending[cursor]
            store.flush()
            payee = store.redis.hget(f"f:dec:{session_id}", "payee")
            results[session_id] = store.get(sender, payee, as_of, session_id)
            cursor += 1
        tick = time.perf_counter()
        store.update(event)
        update_seconds += time.perf_counter() - tick
        seen += 1
        if seen % 500_000 == 0:
            print(f"  replay {seen}", flush=True)
    store.flush()
    while cursor < len(pending):
        as_of, session_id, sender = pending[cursor]
        payee = store.redis.hget(f"f:dec:{session_id}", "payee")
        results[session_id] = store.get(sender, payee, as_of, session_id)
        cursor += 1
    elapsed = time.perf_counter() - started
    times = np.array(store.get_times, dtype=float)
    stats = {
        "events": seen,
        "sessions": len(results),
        "replay_s": elapsed,
        "update_s": update_seconds,
        "redis_bytes": store.memory_bytes(),
        "get_p95_s": float(np.percentile(times, 95)) if len(times) else 0.0,
    }
    return results, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observed", type=Path, default=OBSERVED)
    parser.add_argument("--until", default=SIM_END)
    parser.add_argument("--redis", default=os.environ.get("REDIS_URL", "redis://localhost:6379/15"))
    args = parser.parse_args()
    results, stats = run(args.observed, None, args.until, args.redis)
    print(f"events {stats['events']}")
    print(f"sessions {stats['sessions']}")
    print(f"replay_s {stats['replay_s']:.3f}")
    print(f"update_s {stats['update_s']:.3f}")
    print(f"redis_bytes {stats['redis_bytes']}")
    print(f"get_p95_s {stats['get_p95_s']:.6f}")
    print(f"columns {len(COLUMNS) + len(FAMILY_COLUMNS)}")


if __name__ == "__main__":
    main()
