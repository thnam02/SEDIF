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

TASKS = ("saved_payee", "new_typed", "new_pasted", "qr")


def key_for(user: str, task: str) -> str:
    return f"beh:v24:{user}:{task}"


def quality_name(sessions: int) -> str:
    count = int(sessions)
    if count >= 8:
        return "high"
    if count >= 3:
        return "medium"
    if count > 0:
        return "low"
    return "none"

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


def redis_cmd() -> list[str]:
    """Cùng Redis với demo_reset. Không có REDIS_HOST thì vào compose qua docker."""
    host = os.environ.get("REDIS_HOST", "").strip()
    if not host:
        return ["docker", "exec", "-i", "sedif-redis-1", "redis-cli", "-n", "15"]
    port = os.environ.get("REDIS_PORT", "6379")
    url = os.environ.get("REDIS_URL", "")
    db = url.rstrip("/").rsplit("/", 1)[-1] if "/" in url else "15"
    if not db.isdigit():
        db = "15"
    return ["redis-cli", "-h", host, "-p", port, "-n", db]


def redis(*args: str) -> str:
    done = subprocess.run([*redis_cmd(), *args], check=True, capture_output=True, text=True)
    return done.stdout


def seed() -> str:
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
                redis("SET", key, payload)
                print("kept", key, "sessions", len(records))
                continue
            redis("DEL", key)
    print("baseline only", kept)
    return kept


def check() -> None:
    """Cảnh C cần baseline new_typed của Trương, đủ medium, trước ngày demo."""
    os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
    reset_key_cache()
    cut = int(datetime(2026, 9, 21, tzinfo=ICT).timestamp() * 1_000_000)
    raw = redis("--raw", "GET", key_for(account_hash(SCENE_C), "new_typed"))
    if not raw.strip():
        raise SystemExit("thiếu baseline new_typed của A0002119")
    records = json.loads(raw).get("records") or []
    prior = [
        row
        for row in records
        if int(row.get("ts") or 0) < cut and int(row.get("eligible_at") or 0) <= cut
    ]
    quality = quality_name(len(prior))
    if len(prior) < 3 or quality not in {"medium", "high"}:
        raise SystemExit(f"A0002119 new_typed: {len(prior)} phiên, chất lượng {quality}")
    for account in DEMO_ACCOUNTS:
        for task in TASKS:
            if account == SCENE_C and task == "new_typed":
                continue
            extra = redis("--raw", "GET", key_for(account_hash(account), task)).strip()
            if extra:
                raise SystemExit(f"baseline thừa {account} {task}")
    print(f"baseline A0002119 new_typed {len(prior)} {quality}")


def main() -> None:
    if "--check" in sys.argv:
        check()
        return
    seed()


if __name__ == "__main__":
    main()
