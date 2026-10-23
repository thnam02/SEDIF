"""Cắt Redis ngoài cửa sổ 7 ngày và ghi RDB tại mốc --until."""

from __future__ import annotations

import argparse
import time

from engine.common.hashing import require_key
from engine.features.online import OnlineStore
from engine.features.stream import to_us

import pandas as pd

MAX_MEMORY = 8 * 1024 ** 3


def configure(store: OnlineStore) -> None:
    store.redis.config_set("maxmemory", MAX_MEMORY)
    store.redis.config_set("maxmemory-policy", "noeviction")


def snapshot(until: str, redis_url: str | None = None) -> None:
    require_key()
    store = OnlineStore(redis_url, flush=False)
    configure(store)
    stamp = int(to_us(pd.Series([pd.Timestamp(until)]))[0])
    store.trim(stamp)
    for _ in range(240):
        if not store.redis.info("persistence").get("rdb_bgsave_in_progress"):
            break
        time.sleep(0.5)
    store.redis.save()
    print(f"snapshot until={until} maxmemory={store.redis.config_get('maxmemory')['maxmemory']} bytes={store.memory_bytes()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--until", required=True)
    parser.add_argument("--redis", default=None)
    args = parser.parse_args()
    snapshot(args.until, args.redis)


if __name__ == "__main__":
    main()
