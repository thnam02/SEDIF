"""Một dòng CSV cho mỗi phiên thí nghiệm. Mã tài khoản được băm."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")

from engine.common.hashing import account_hash

_ACCOUNT = re.compile(r"^[A-Z][A-Z0-9]{5,}$")
_ACCOUNT_KEYS = {"account", "account_id", "from_account", "to_account", "payee", "sender", "counterparty"}


def _is_hmac(text: str) -> bool:
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def hash_accounts(value):
    if isinstance(value, dict):
        hashed = {}
        for key, item in value.items():
            if key in _ACCOUNT_KEYS and isinstance(item, str) and not _is_hmac(item):
                hashed[key] = account_hash(item)
            else:
                hashed[key] = hash_accounts(item)
        return hashed
    if isinstance(value, list):
        return [hash_accounts(item) for item in value]
    return value


def hash_text(value: str) -> str:
    if _is_hmac(value):
        return value
    if _ACCOUNT.fullmatch(value) and any(char.isdigit() for char in value):
        return account_hash(value)
    return value


def _load_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def export_row(record: dict) -> dict:
    call = _load_json(record.get("call")) or {}
    features = hash_accounts(_load_json(record.get("behaviour_features")) or {})
    decision = _load_json(record.get("decision")) or {}
    card = decision.get("evidence_card") or {}
    rules = decision.get("rules_fired") or []
    reason_codes = [item.get("code") for item in (decision.get("reasons") or []) if isinstance(item, dict) and item.get("code")]
    return {
        "participant": hash_text(str(record.get("participant_id") or "")),
        "condition": str(record.get("condition") or ""),
        "session_index": record.get("session_index"),
        "task_type": str(record.get("task_type") or ""),
        "call_state": str(call.get("state") or ""),
        "features": json.dumps(features, ensure_ascii=False),
        "behaviour_score": card.get("behaviour_score"),
        "p_rarity": card.get("p_rarity"),
        "baseline_quality": card.get("baseline_quality") or "",
        "level": decision.get("level"),
        "rules_fired": json.dumps(rules, ensure_ascii=False),
        "reason_codes": json.dumps(reason_codes, ensure_ascii=False),
    }


COLUMNS = (
    "participant",
    "condition",
    "session_index",
    "task_type",
    "call_state",
    "features",
    "behaviour_score",
    "p_rarity",
    "baseline_quality",
    "level",
    "rules_fired",
    "reason_codes",
)


def _rows() -> list[dict]:
    import psycopg

    url = os.environ.get("DATABASE_URL", "postgresql://hainamtran@localhost:5432/fides_w1")
    with psycopg.connect(url, row_factory=psycopg.rows.dict_row) as conn:
        fetched = conn.execute(
            """
            SELECT participant_id, condition, session_index, task_type, call, behaviour_features, decision
            FROM bank.experiment_sessions
            ORDER BY participant_id, session_index, created_at
            """
        ).fetchall()
    return [export_row(dict(row)) for row in fetched]


def write_csv(rows: list[dict], destination) -> None:
    writer = csv.DictWriter(destination, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: "" if row.get(name) is None else row.get(name) for name in COLUMNS})


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = _rows()
    if target:
        with open(target, "w", encoding="utf-8", newline="") as handle:
            write_csv(rows, handle)
        print(f"wrote {len(rows)} {target}")
        return
    write_csv(rows, sys.stdout)


if __name__ == "__main__":
    main()
