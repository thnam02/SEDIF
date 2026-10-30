"""Bank-core chuyển nguyên khối v2.4 sang /decide và chỉ ghi hash vào bảng thí nghiệm."""

from __future__ import annotations

import json
import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "bank-core"), str(REPO)]
os.environ["FIDES_HMAC_KEY"] = "fides-demo-hmac"
os.environ["FIDES_API_KEY"] = "fides-demo-key"
os.environ["DATABASE_URL"] = "postgresql://hainamtran@localhost:5432/fides_w1"
os.environ["DEMO_REPLAY"] = "0"
os.environ["BANK_RESEED"] = "0"

from engine.common.hashing import account_hash, reset_key_cache

SENDER = "A0002119"
PAYEE = "A0000623"
DATABASE = os.environ["DATABASE_URL"]

CALL = {"state": "active", "observability": "supported_observed", "transport": "unknown"}
FEATURES = {
    "median_gap_ms": 420,
    "p90_gap_ms": 1800,
    "burst_count": 3,
    "pause_to_action_ratio": 0.15,
    "review_dwell_ms": 900,
    "edit_count": 1,
    "backtrack_count": 0,
    "screen_revisit_count": 0,
    "background_count": 1,
    "background_total_ms": 3000,
    "session_duration_ms": 10000,
}
EXPERIMENT = {"participant_id": "p-pytest-v24", "condition": "control", "session_index": 2}
DEVICE = {"in_call": True, "acct_input": "pasted", "state": "unknown"}


def _load():
    reset_key_cache()
    spec = spec_from_file_location("bank_core_v24", REPO / "bank-core" / "app.py")
    module = module_from_spec(spec)
    sys.modules["bank_core_v24"] = module
    spec.loader.exec_module(module)
    return module


def _balance(account: str) -> int:
    with psycopg.connect(DATABASE) as conn:
        row = conn.execute("SELECT balance FROM bank.accounts WHERE id = %s", (account,)).fetchone()
    assert row is not None
    return int(row[0])


def _restore_transfer(sender: str, balance: int) -> None:
    with psycopg.connect(DATABASE) as conn:
        rows = conn.execute("SELECT id FROM bank.transfers WHERE purpose_text = %s", ("pytest-v24",)).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            conn.execute("DELETE FROM bank.entries WHERE tx_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM bank.guardian_requests WHERE transfer_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM bank.transfers WHERE id = ANY(%s)", (ids,))
        conn.execute("UPDATE bank.accounts SET balance = %s WHERE id = %s", (balance, sender))
        conn.commit()


def _drop_experiment(participant_id: str) -> None:
    with psycopg.connect(DATABASE) as conn:
        conn.execute("DELETE FROM bank.experiment_sessions WHERE participant_id = %s", (participant_id,))
        conn.commit()


@pytest.fixture(scope="module")
def bank():
    module = _load()
    seen: list[tuple[str, dict]] = []

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        seen.append((path, body))
        if path == "/decide":
            return {
                "level": "none",
                "funds_status": "held",
                "hold_until": None,
                "countdown_until": None,
                "actions": [],
                "reasons": [],
                "families_hit": [],
            }
        return {}

    module.engine_post = engine_post
    with TestClient(module.app) as client:
        yield module, client, seen


def test_decide_receives_v24_fields_unchanged(bank):
    _module, client, seen = bank
    before = _balance(SENDER)
    payload = {
        "from_account": SENDER,
        "to_account": PAYEE,
        "amount": 1000,
        "purpose_text": "pytest-v24",
        "task_type": "new_pasted",
        "call": CALL,
        "behaviour_features": FEATURES,
        "transfer_purpose": "not_asked",
        "device": DEVICE,
        "experiment": EXPERIMENT,
    }
    try:
        response = client.post("/transfer", json=payload)
        assert response.status_code == 200, response.text
        decided = [body for path, body in seen if path == "/decide"]
        assert len(decided) == 1
        forwarded = decided[0]
        assert forwarded["task_type"] == payload["task_type"]
        assert forwarded["call"] == payload["call"]
        assert forwarded["behaviour_features"] == payload["behaviour_features"]
        assert forwarded["transfer_purpose"] == payload["transfer_purpose"]
        assert forwarded["device"] == payload["device"]
        assert forwarded["experiment"] == payload["experiment"]
    finally:
        _restore_transfer(SENDER, before)


def test_experiment_session_is_written(bank):
    _module, client, _seen = bank
    participant = "p-pytest-v24-row"
    body = {
        "participant_id": participant,
        "condition": "control",
        "session_index": 4,
        "task_type": "qr",
        "call": CALL,
        "behaviour_features": FEATURES,
        "raw_events": [{"type": "confirm_tap", "t": 10}],
    }
    try:
        response = client.post("/experiment/sessions", json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"stored": True}
        with psycopg.connect(DATABASE, row_factory=psycopg.rows.dict_row) as conn:
            row = conn.execute(
                """
                SELECT participant_id, condition, session_index, task_type, call, behaviour_features, raw_events, created_at
                FROM bank.experiment_sessions
                WHERE participant_id = %s
                """,
                (participant,),
            ).fetchone()
        assert row is not None
        assert row["participant_id"] == participant
        assert row["condition"] == "control"
        assert row["session_index"] == 4
        assert row["task_type"] == "qr"
        assert row["call"] == CALL
        assert row["behaviour_features"] == FEATURES
        assert row["raw_events"] == [{"type": "confirm_tap", "t": 10}]
        assert row["created_at"] is not None
    finally:
        _drop_experiment(participant)


def test_experiment_session_stores_hashed_accounts(bank):
    _module, client, _seen = bank
    participant = "p-pytest-v24-hash"
    raw_account = "A0002119"
    other_account = "ZVAY000000000001"
    body = {
        "participant_id": participant,
        "condition": "treatment",
        "session_index": 1,
        "task_type": "saved_payee",
        "call": CALL,
        "behaviour_features": FEATURES,
        "raw_events": [
            {"type": "saved_payee_pick", "t": 1, "account_id": raw_account},
            {"type": "screen_enter", "t": 2, "screen": "confirm", "nested": {"to_account": other_account}},
        ],
    }
    try:
        response = client.post("/experiment/sessions", json=body)
        assert response.status_code == 200, response.text
        with psycopg.connect(DATABASE, row_factory=psycopg.rows.dict_row) as conn:
            row = conn.execute(
                "SELECT call, behaviour_features, raw_events FROM bank.experiment_sessions WHERE participant_id = %s",
                (participant,),
            ).fetchone()
        assert row is not None
        stored = json.dumps(row, default=str)
        assert raw_account not in stored
        assert other_account not in stored
        events = row["raw_events"]
        assert events[0]["account_id"] == account_hash(raw_account)
        assert events[1]["nested"]["to_account"] == account_hash(other_account)
        assert len(events[0]["account_id"]) == 64
        assert events[1]["screen"] == "confirm"
    finally:
        _drop_experiment(participant)
