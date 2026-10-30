"""Chỉ Trương Gia Thịnh (A0002119, cảnh C) có baseline. Các tài khoản demo khác bị xoá khoá beh, không flush."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO)]

from engine.common.hashing import account_hash, reset_key_cache
from engine.features.behaviour import TASKS, key_for

# Mọi tài khoản của bốn khách demo. Chỉ A0002119 được giữ baseline new_typed.
DEMO_ACCOUNTS = (
    "A0000623",
    "A0050272",
    "A0072556",
    "A0077690",
    "A0002119",
    "A0002845",
    "A0051289",
    "A0072782",
    "A0078375",
    "A0097589",
    "A0031239",
)
SCENE_C = "A0002119"

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
ORDINARY = {
    "median_gap_ms": 805.2112291861524,
    "p90_gap_ms": 2206.6761287252866,
    "pause_to_action_ratio": 0.1799915702151184,
    "review_dwell_ms": 1409.8633588362977,
    "background_total_ms": 1802.2294266325746,
    "burst_count": 2.0,
    "edit_count": 1.0,
    "backtrack_count": 0.0,
    "screen_revisit_count": 1.0,
    "background_count": 1.0,
    "session_duration_ms": 41591.766409080185,
}


def main() -> None:
    os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
    reset_key_cache()
    kept = account_hash(SCENE_C)
    start = datetime(2026, 9, 1, tzinfo=ICT)
    records = []
    for index in range(10):
        stamp = start + timedelta(days=index)
        ts = int(stamp.timestamp() * 1_000_000)
        records.append({"ts": ts, "eligible_at": ts, "features": dict(ORDINARY)})
    payload = json.dumps({"records": records}, ensure_ascii=False)
    for account in DEMO_ACCOUNTS:
        user = account_hash(account)
        for task in TASKS:
            key = key_for(user, task)
            if account == SCENE_C and task == "new_typed":
                subprocess.run(
                    ["docker", "exec", "-i", "sedif-redis-1", "redis-cli", "-n", "15", "SET", key, payload],
                    check=True,
                )
                print("kept", key, "sessions", len(records))
                continue
            subprocess.run(
                ["docker", "exec", "-i", "sedif-redis-1", "redis-cli", "-n", "15", "DEL", key],
                check=True,
            )
    print("baseline only", kept)


if __name__ == "__main__":
    main()
