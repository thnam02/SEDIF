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
            "-c",
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
