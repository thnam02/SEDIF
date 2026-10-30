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


def isolate(accounts: tuple[str, ...]) -> None:
    for account in accounts:
        user = account_hash(account)
        redis("DEL", f"pol:v24:{user}")
        if account == "A0002119":
            continue
        for task in TASKS:
            redis("DEL", key_for(user, task))
    listed = ",".join(f"'{account}'" for account in accounts)
    unfinished = "('pending_draft', 'needs_verification', 'held', 'blocked_pending_hangup')"
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
            f"DELETE FROM bank.products WHERE account_id IN ({listed}) AND channel IN ('app','branch') AND id <> 'P-demo-branch-closure';",
        ],
        check=True,
    )


def main() -> None:
    subprocess.run([sys.executable, str(REPO / "scripts" / "seed_demo_baseline.py")], check=True)
    failed = []
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
    if failed:
        print("failed", " ".join(failed), flush=True)
        raise SystemExit(1)
    print("scenes passed in reverse order", flush=True)


if __name__ == "__main__":
    main()
