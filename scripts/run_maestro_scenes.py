"""Chạy các cảnh Maestro từ F về A. Xoá episode của đúng khách đó trước mỗi cảnh. Không flush Redis."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO)]

os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")

from engine.common.hashing import account_hash, reset_key_cache
from engine.features.behaviour import TASKS, key_for

reset_key_cache()

DEVICE = "28BF45AF-7886-4022-8152-60882E6355D4"
HUYNH = ("A0000623", "A0050272", "A0072556", "A0077690")
HOANG = ("A0002845", "A0051289", "A0072782", "A0078375", "A0097589")
TRUONG = ("A0002119",)
LE = ("A0031239",)
# Ngược thứ tự A…F để mức không phụ thuộc cảnh chạy trước.
ORDER = (
    ("scene-f.yaml", HUYNH),
    ("scene-e.yaml", HOANG),
    ("scene-d.yaml", HOANG),
    ("scene-c.yaml", TRUONG),
    ("scene-b-ended.yaml", HUYNH),
    ("scene-b.yaml", HUYNH),
    ("scene-a.yaml", LE),
)


def redis(*args: str) -> None:
    subprocess.run(["docker", "exec", "-i", "sedif-redis-1", "redis-cli", "-n", "15", *args], check=True)


def scrub_demo_window(accounts: tuple[str, ...]) -> None:
    """Bỏ dấu demo từ 20/09 trở đi để cửa sổ 24 giờ không cộng lệnh của cảnh trước."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    cut = int(datetime(2026, 9, 20, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).timestamp() * 1_000_000)
    hashes = ",".join(account_hash(account) for account in accounts)
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"CUT={cut}",
            "-e",
            f"HASHES={hashes}",
            "sedif-engine-1",
            "python",
            "-c",
            """
import os, redis
client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
cut = int(os.environ["CUT"])
def scrub(key):
    for field, value in client.hscan_iter(key, count=400):
        parts = value.split("|")
        raw = parts[1] if len(parts) >= 3 else value
        try:
            stamp = int(raw)
        except ValueError:
            continue
        if stamp >= cut:
            client.hdel(key, field)
for digest in os.environ["HASHES"].split(","):
    if not digest:
        continue
    for prefix in ("f:out:", "f:fresh:", "f:xf:", "f:in:"):
        client.zremrangebyscore(prefix + digest, cut, "+inf")
    for kind in ("savings_liquidation", "quick_loan_disbursement", "limit_raise", "credit_card_cash_advance", "term_deposit_closure"):
        client.zremrangebyscore(f"f:prod:{kind}:{digest}", cut, "+inf")
    scrub(f"f:fst:{digest}")
    customer = client.hget(f"f:meta:{digest}", "customer")
    if customer:
        scrub(f"f:fstc:{customer}")
""",
        ],
        check=True,
    )


def isolate(accounts: tuple[str, ...]) -> None:
    scrub_demo_window(accounts)
    for account in accounts:
        user = account_hash(account)
        redis("DEL", f"pol:v24:{user}")
        if account == "A0002119":
            continue
        for task in TASKS:
            redis("DEL", key_for(user, task))
    listed = ",".join(f"'{account}'" for account in accounts)
    # released: giám hộ đã cho đi, tiền đã trừ. Cảnh sau của cùng khách phải thấy lại số dư đầu cảnh.
    unfinished = "('pending_draft', 'needs_verification', 'held', 'blocked_pending_hangup', 'released')"
    # Lệnh chưa xong đã trừ số dư lúc ghi sổ. Xoá dòng mà không hoàn tiền thì cảnh sau hết số dư.
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "sedif-postgres-1",
            "psql",
            "-U",
            "fides",
            "-d",
            "fides",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"UPDATE bank.accounts a SET balance = a.balance - s.amount FROM ("
            f"SELECT to_account, SUM(amount) AS amount FROM bank.transfers "
            f"WHERE from_account IN ({listed}) AND status = 'released' AND posted GROUP BY to_account"
            f") s WHERE a.id = s.to_account;"
            f"UPDATE bank.accounts a SET balance = a.balance + s.amount FROM ("
            f"SELECT from_account, SUM(amount) AS amount FROM bank.transfers "
            f"WHERE from_account IN ({listed}) AND status IN {unfinished} GROUP BY from_account"
            f") s WHERE a.id = s.from_account;"
            f"UPDATE bank.accounts a SET balance = a.balance + s.amount FROM ("
            f"SELECT e.account_id, SUM(e.amount) AS amount FROM bank.entries e "
            f"WHERE e.account_id IN ({listed}) AND e.direction = 'out' AND e.tx_id LIKE 'X-%' "
            f"AND NOT EXISTS (SELECT 1 FROM bank.transfers t WHERE t.id = e.tx_id) GROUP BY e.account_id"
            f") s WHERE a.id = s.account_id;"
            f"DELETE FROM bank.entries e WHERE e.account_id IN ({listed}) AND e.tx_id LIKE 'X-%' "
            f"AND NOT EXISTS (SELECT 1 FROM bank.transfers t WHERE t.id = e.tx_id);"
            f"DELETE FROM bank.entries WHERE tx_id IN ("
            f"SELECT id FROM bank.transfers WHERE from_account IN ({listed}) AND status IN {unfinished});"
            f"DELETE FROM bank.guardian_requests WHERE account_id IN ({listed});"
            f"DELETE FROM bank.idempotency WHERE transfer_id IN ("
            f"SELECT id FROM bank.transfers WHERE from_account IN ({listed}) AND status IN {unfinished});"
            f"DELETE FROM bank.transfers WHERE from_account IN ({listed}) AND status IN {unfinished};"
            f"DELETE FROM bank.idempotency WHERE transfer_id IN ("
            f"SELECT id FROM bank.transfers WHERE from_account IN ({listed}) AND status = 'awaiting_confirmation');"
            f"DELETE FROM bank.transfers WHERE from_account IN ({listed}) AND status = 'awaiting_confirmation';"
            f"UPDATE bank.accounts a SET balance = a.balance + s.amount, "
            f"status = CASE WHEN a.account_type = 'savings' THEN 'open' ELSE a.status END FROM ("
            f"SELECT account_id, SUM(amount) AS amount FROM bank.entries "
            f"WHERE account_id IN ({listed}) AND direction = 'out' AND left(tx_id, 2) = 'P-' "
            f"GROUP BY account_id) s WHERE a.id = s.account_id;"
            f"UPDATE bank.accounts a SET balance = a.balance - s.amount FROM ("
            f"SELECT account_id, SUM(amount) AS amount FROM bank.entries "
            f"WHERE account_id IN ({listed}) AND direction = 'in' AND left(tx_id, 2) = 'P-' "
            f"GROUP BY account_id) s WHERE a.id = s.account_id;"
            f"DELETE FROM bank.entries WHERE account_id IN ({listed}) AND left(tx_id, 2) = 'P-';"
            f"DELETE FROM bank.products WHERE account_id IN ({listed}) AND channel IN ('app','branch') AND id <> 'P-demo-branch-closure';",
        ],
        check=True,
    )


def lan_address() -> str:
    for iface in ("en0", "en1"):
        probe = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout.strip():
            return probe.stdout.strip()
    raise RuntimeError("no LAN address for the compose database")


def demo_reset() -> None:
    ip = lan_address()
    env = os.environ.copy()
    env.update(
        {
            "BANK_URL": "http://127.0.0.1:8082",
            "ENGINE_URL": "http://127.0.0.1:8081",
            "DATABASE_URL": f"postgresql://fides:fides@{ip}:5432/fides",
            "REDIS_MODE": "docker",
            "REDIS_HOST": ip,
            "REDIS_PORT": "6379",
            "REDIS_URL": f"redis://{ip}:6379/15",
        }
    )
    print("demo_reset", flush=True)
    subprocess.run(["/bin/sh", str(REPO / "scripts" / "demo_reset.sh")], check=True, env=env)


def main() -> None:
    failed: list[str] = []
    try:
        subprocess.run([sys.executable, str(REPO / "scripts" / "seed_demo_baseline.py")], check=True)
        for name, accounts in ORDER:
            print(f"isolate {name} {','.join(accounts)}", flush=True)
            isolate(accounts)
            result = subprocess.run(
                [
                    str(Path.home() / ".maestro" / "bin" / "maestro"),
                    "--device",
                    DEVICE,
                    "test",
                    f".maestro/{name}",
                ],
                cwd=REPO / "bank-app",
            )
            if result.returncode != 0:
                failed.append(name)
    finally:
        if failed:
            print("failed", " ".join(failed), flush=True)
        demo_reset()
    if failed:
        raise SystemExit(1)
    print("scenes passed in reverse order", flush=True)


if __name__ == "__main__":
    main()
