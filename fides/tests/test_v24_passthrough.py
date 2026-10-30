"""Bank-core chuyển nguyên khối v2.4 sang /decide và chỉ ghi hash vào bảng thí nghiệm."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
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


def _restore_transfer(sender: str, balance: int, purposes: tuple[str, ...] = ("pytest-v24",)) -> None:
    with psycopg.connect(DATABASE) as conn:
        rows = conn.execute("SELECT id FROM bank.transfers WHERE purpose_text = ANY(%s)", (list(purposes),)).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            conn.execute("DELETE FROM bank.entries WHERE tx_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM bank.guardian_requests WHERE transfer_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM bank.transfers WHERE id = ANY(%s)", (ids,))
        conn.execute("UPDATE bank.accounts SET balance = %s WHERE id = %s", (balance, sender))
        conn.commit()


def _set_balance(account: str, balance: int) -> None:
    with psycopg.connect(DATABASE) as conn:
        conn.execute("UPDATE bank.accounts SET balance = %s WHERE id = %s", (balance, account))
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
    payee_before = _balance(PAYEE)
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
        _set_balance(PAYEE, payee_before)
        _drop_experiment(EXPERIMENT["participant_id"])


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


def test_export_row_hashes_account_ids():
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("export_experiment", REPO / "scripts" / "export_experiment.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    row = module.export_row(
        {
            "participant_id": SENDER,
            "condition": "control",
            "session_index": 1,
            "task_type": "new_typed",
            "call": CALL,
            "behaviour_features": {"account_id": "ZVAY000000000001", "median_gap_ms": 10},
            "decision": {
                "level": 2,
                "rules_fired": ["R_BEHAVIOUR_GUIDED"],
                "evidence_card": {"behaviour_score": 0.4, "p_rarity": 0.03, "baseline_quality": "high"},
            },
        }
    )
    encoded = json.dumps(row)
    assert row["participant"] == account_hash(SENDER)
    assert SENDER not in encoded
    assert "ZVAY000000000001" not in encoded
    assert row["call_state"] == "active"
    assert row["behaviour_score"] == 0.4
    assert row["p_rarity"] == 0.03
    assert row["baseline_quality"] == "high"
    assert row["level"] == 2
    assert row["rules_fired"] == '["R_BEHAVIOUR_GUIDED"]'


def test_experiment_transfer_completes_and_a_plain_transfer_still_holds(bank):
    module, client, seen = bank
    original = module.engine_post
    participant = "p-pytest-exp-complete"
    before = _balance(SENDER)
    payee_before = _balance(PAYEE)

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        seen.append((path, body))
        if path == "/decide":
            return {
                "level": 2,
                "rules_fired": ["R_BEHAVIOUR_GUIDED"],
                "evidence_card": {
                    "signals": [],
                    "reasons_vi": ["dừng lâu hơn thường lệ"],
                    "baseline_quality": "medium",
                    "episode_id": "",
                    "behaviour_score": 1.2,
                    "p_rarity": 0.02,
                },
                "funds_status": "held",
                "hold_until": "2026-09-21T10:00:00+07:00",
                "countdown_until": None,
                "actions": ["verify"],
                "reasons": [{"code": "v24_0", "text": "khác thường"}],
                "families_hit": [],
            }
        return {}

    module.engine_post = engine_post
    shared = {
        "from_account": SENDER,
        "to_account": PAYEE,
        "amount": 1000,
        "task_type": "new_pasted",
        "call": CALL,
        "behaviour_features": FEATURES,
        "transfer_purpose": "not_asked",
        "device": DEVICE,
    }
    try:
        response = client.post(
            "/transfer",
            json={**shared, "purpose_text": "pytest-exp", "experiment": {**EXPERIMENT, "participant_id": participant}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "done"
        assert body["funds_status"] == "released"
        assert body["decision"]["level"] == "medium"
        assert body["decision"]["engine_level"] == 2
        assert body["decision"]["rules_fired"] == ["R_BEHAVIOUR_GUIDED"]
        assert body["decision"]["evidence_card"]["behaviour_score"] == 1.2
        assert body["decision"]["evidence_card"]["p_rarity"] == 0.02
        with psycopg.connect(DATABASE, row_factory=psycopg.rows.dict_row) as conn:
            stored = conn.execute(
                "SELECT decision FROM bank.experiment_sessions WHERE participant_id = %s",
                (participant,),
            ).fetchone()
        assert stored is not None
        assert stored["decision"]["level"] == 2
        assert stored["decision"]["rules_fired"] == ["R_BEHAVIOUR_GUIDED"]
        assert stored["decision"]["evidence_card"]["baseline_quality"] == "medium"
        plain = client.post("/transfer", json={**shared, "purpose_text": "pytest-exp-plain"})
        assert plain.status_code == 200, plain.text
        held = plain.json()
        assert held["status"] == "needs_verification"
        assert held["funds_status"] == "held"
        assert held["decision"]["engine_level"] == 2
        assert held["decision"]["intervention"] == "three_button"
        assert held["decision"]["rules_fired"] == ["R_BEHAVIOUR_GUIDED"]
        assert held["decision"]["evidence_card"]["reasons_vi"] == ["dừng lâu hơn thường lệ"]
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-v24", "pytest-exp", "pytest-exp-plain"))
        _set_balance(PAYEE, payee_before)
        _drop_experiment(participant)


def test_purpose_threshold_comes_from_the_server(bank):
    _module, client, _seen = bank
    response = client.get("/config")
    assert response.status_code == 200, response.text
    assert response.json()["purpose_amount_vnd"] == 10_000_000


def test_choice_pause_holds_a_draft_and_cancel_refunds(bank):
    module, client, seen = bank
    original = module.engine_post
    before = _balance(SENDER)
    payee_before = _balance(PAYEE)

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        seen.append((path, body))
        if path == "/decide":
            return {
                "level": 2,
                "intervention": "three_button",
                "rules_fired": ["R_PURPOSE_HIGH_RISK"],
                "evidence_card": {"signals": [], "reasons_vi": ["Mục đích chuyển là yêu cầu của cơ quan hoặc phí để nhận tiền."], "baseline_quality": "none", "episode_id": ""},
                "funds_status": "held",
                "hold_until": None,
                "countdown_until": None,
                "actions": [],
                "reasons": [],
                "families_hit": [],
            }
        if path.endswith("/choice"):
            return {"decision_id": path.split("/")[2], "choice": body["choice"], "logged": True}
        return {}

    module.engine_post = engine_post
    try:
        paused = client.post("/transfer", json={"from_account": SENDER, "to_account": PAYEE, "amount": 1000, "purpose_text": "pytest-choice-pause", "transfer_purpose": "authority_request"})
        assert paused.status_code == 200, paused.text
        draft = paused.json()
        choice = client.post(f"/decisions/dec-{draft['transfer_id']}/choice", json={"choice": "pause", "response_ms": 1200})
        assert choice.status_code == 200, choice.text
        assert choice.json()["status"] == "pending_draft"
        assert choice.json()["funds_status"] == "held"
        assert choice.json()["logged"] is True
        assert _balance(SENDER) == before - 1000
        assert any(path.endswith("/choice") and body.get("response_ms") == 1200 for path, body in seen)
        cancelled = client.post("/transfer", json={"from_account": SENDER, "to_account": PAYEE, "amount": 1000, "purpose_text": "pytest-choice-cancel", "transfer_purpose": "authority_request"})
        assert cancelled.status_code == 200, cancelled.text
        undone = client.post(f"/decisions/dec-{cancelled.json()['transfer_id']}/choice", json={"choice": "cancel", "response_ms": 400})
        assert undone.status_code == 200, undone.text
        assert undone.json()["status"] == "cancelled"
        assert undone.json()["funds_status"] == "cancelled"
        assert _balance(SENDER) == before - 1000
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-choice-pause", "pytest-choice-cancel"))
        _set_balance(PAYEE, payee_before)


def test_guardian_is_notified_only_after_consent(bank):
    module, client, _seen = bank
    original = module.engine_post
    before = _balance(SENDER)

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        if path == "/decide":
            return {
                "level": 2,
                "intervention": "three_button",
                "rules_fired": [],
                "evidence_card": {"signals": [], "reasons_vi": [], "baseline_quality": "none", "episode_id": ""},
                "funds_status": "held",
                "hold_until": None,
                "actions": [],
                "reasons": [],
                "families_hit": [],
            }
        return {}

    module.engine_post = engine_post
    with psycopg.connect(DATABASE, row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("ALTER TABLE bank.guardians ADD COLUMN IF NOT EXISTS consent boolean NOT NULL DEFAULT false")
        prior = conn.execute("SELECT guardian_phone_hash, relation, registered_at, consent FROM bank.guardians WHERE account_id = %s", (SENDER,)).fetchone()
        flag = conn.execute("SELECT has_guardian FROM bank.accounts WHERE id = %s", (SENDER,)).fetchone()
        conn.execute("UPDATE bank.guardians SET consent = false WHERE account_id = %s", (SENDER,))
        conn.commit()
    try:
        refused = client.post("/guardian", json={"account_id": SENDER, "phone": "0900000001", "relation": "con", "consent": False})
        assert refused.status_code == 400, refused.text
        silent = client.post("/transfer", json={"from_account": SENDER, "to_account": PAYEE, "amount": 1000, "purpose_text": "pytest-guard-silent"})
        assert silent.status_code == 200, silent.text
        with psycopg.connect(DATABASE) as conn:
            quiet = conn.execute("SELECT 1 FROM bank.guardian_requests WHERE transfer_id = %s", (silent.json()["transfer_id"],)).fetchone()
        assert quiet is None
        accepted = client.post("/guardian", json={"account_id": SENDER, "phone": "0900000001", "relation": "con", "consent": True})
        assert accepted.status_code == 200, accepted.text
        noisy = client.post("/transfer", json={"from_account": SENDER, "to_account": PAYEE, "amount": 1000, "purpose_text": "pytest-guard-notify"})
        assert noisy.status_code == 200, noisy.text
        with psycopg.connect(DATABASE) as conn:
            hit = conn.execute("SELECT status FROM bank.guardian_requests WHERE transfer_id = %s", (noisy.json()["transfer_id"],)).fetchone()
        assert hit is not None
        assert hit[0] == "open"
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-guard-silent", "pytest-guard-notify"))
        with psycopg.connect(DATABASE) as conn:
            if prior is None:
                conn.execute("DELETE FROM bank.guardians WHERE account_id = %s", (SENDER,))
            else:
                conn.execute(
                    """
                    INSERT INTO bank.guardians (account_id, guardian_phone_hash, relation, registered_at, consent)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (account_id) DO UPDATE
                    SET guardian_phone_hash=EXCLUDED.guardian_phone_hash, relation=EXCLUDED.relation,
                        registered_at=EXCLUDED.registered_at, consent=EXCLUDED.consent
                    """,
                    (SENDER, prior["guardian_phone_hash"], prior["relation"], prior["registered_at"], prior["consent"]),
                )
            conn.execute("UPDATE bank.accounts SET has_guardian = %s WHERE id = %s", (bool(flag["has_guardian"]) if flag else False, SENDER))
            conn.commit()


def test_ledger_closure_active_call_and_new_payee_is_level_2(bank):
    from engine.policy.v24 import evaluate, facts_from_row

    module, client, _seen = bank
    original = module.engine_post
    captured: list[dict] = []

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        if path == "/decide":
            captured.append(body)
        return original(path, body, timeout)

    module.engine_post = engine_post
    before = _balance(SENDER)
    product_id = None
    try:
        closed = client.post("/savings/liquidate", json={"account_id": SENDER, "amount": 10000})
        assert closed.status_code == 200, closed.text
        product_id = closed.json()["id"]
        response = client.post(
            "/transfer",
            json={
                "from_account": SENDER,
                "to_account": "ZLEDGER000000001",
                "amount": 1000,
                "purpose_text": "pytest-ledger-closure",
                "task_type": "new_typed",
                "call": CALL,
                "transfer_purpose": "purchase",
                "device": {"state": "unknown"},
                "recent_funds_events": [{"type": "not_a_ledger_event", "at": "2026-09-20T00:00:00+07:00", "channel": "app"}],
            },
        )
        assert response.status_code == 200, response.text
        assert captured, "không thấy /decide"
        body = captured[-1]
        kinds = [item["type"] for item in body["recent_funds_events"]]
        assert "savings_liquidation" in kinds
        assert "not_a_ledger_event" not in kinds
        assert body["call"]["state"] == "active"
        decided = datetime.fromisoformat(body["ts"])
        facts = facts_from_row(body, {"new_payee": 1, "segment": "thường"}, 0.0, decided, None)
        out = evaluate(facts)
        assert out.level == 2
        assert "R_CALL_FUNDS_NEWPAYEE" in out.rules_fired
        card = {item.signal_id: item for item in out.signals}
        assert card["has_call"].status == "present" and card["has_call"].source == "sdk"
        assert card["recent_funds_event"].status == "present" and card["recent_funds_event"].source == "core_banking"
        assert card["beneficiary_is_new"].status == "present" and card["beneficiary_is_new"].source == "core_banking"
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-ledger-closure",))
        if product_id:
            with psycopg.connect(DATABASE) as conn:
                conn.execute("DELETE FROM bank.entries WHERE tx_id = %s", (product_id,))
                conn.execute("DELETE FROM bank.products WHERE id = %s", (product_id,))
                conn.commit()
