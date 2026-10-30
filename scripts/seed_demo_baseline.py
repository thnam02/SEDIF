"""Ghi 10 phiên new_typed đã đủ hạn cho tài khoản demo A0002119. Chỉ một khoá Redis, không flush."""

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
from engine.features.behaviour import key_for

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
    user = account_hash("A0002119")
    start = datetime(2026, 9, 1, tzinfo=ICT)
    records = []
    for index in range(10):
        stamp = start + timedelta(days=index)
        ts = int(stamp.timestamp() * 1_000_000)
        records.append({"ts": ts, "eligible_at": ts, "features": dict(ORDINARY)})
    payload = json.dumps({"records": records}, ensure_ascii=False)
    key = key_for(user, "new_typed")
    subprocess.run(
        ["docker", "exec", "-i", "sedif-redis-1", "redis-cli", "-n", "15", "SET", key, payload],
        check=True,
    )
    print(key, "sessions", len(records))


if __name__ == "__main__":
    main()
