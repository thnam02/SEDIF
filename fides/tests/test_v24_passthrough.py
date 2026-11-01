"""Bank-core chuyển nguyên khối v2.4 sang /decide và chỉ ghi hash vào bảng thí nghiệm."""

from __future__ import annotations

import json
import os
import sys
import time
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
                "signals_present": [],
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
                "reasons": [{"code": "call_active", "text": "Có cuộc gọi đang diễn ra trong phiên này."}, {"code": "new_payee", "text": "Chưa từng chuyển tới người nhận này."}],
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
    assert row["reason_codes"] == '["call_active", "new_payee"]'


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
                "signals_present": [],
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
        assert body["decision"]["level"] == 2
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
        assert held["decision"]["level"] == 2
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
                "signals_present": [],
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
                "signals_present": [],
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
        posted = [item for item in body["recent_funds_events"] if item["type"] == "savings_liquidation"]
        assert any(item["channel"] == "app" for item in posted)
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


def test_ledger_channel_unknown_does_not_count_and_branch_does(bank):
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
    ids = ("P-pytest-channel-unknown", "P-pytest-channel-branch")
    try:
        with psycopg.connect(DATABASE) as conn:
            module.ensure_fund_channel(conn)
            conn.execute(
                "INSERT INTO bank.products (id, account_id, amount, kind, ts, channel) VALUES (%s,%s,1,'savings_liquidation',%s,NULL)",
                (ids[0], SENDER, datetime.fromisoformat("2026-09-19T12:00:00+07:00")),
            )
            conn.execute(
                "INSERT INTO bank.products (id, account_id, amount, kind, ts, channel) VALUES (%s,%s,1,'savings_liquidation',%s,'branch')",
                (ids[1], SENDER, datetime.fromisoformat("2026-09-19T13:00:00+07:00")),
            )
            conn.commit()
        response = client.post(
            "/transfer",
            json={
                "from_account": SENDER,
                "to_account": "ZLEDGER000000002",
                "amount": 1000,
                "purpose_text": "pytest-ledger-channel",
                "task_type": "new_typed",
                "call": CALL,
                "transfer_purpose": "purchase",
                "device": {"state": "unknown"},
            },
        )
        assert response.status_code == 200, response.text
        body = captured[-1]
        noon = datetime.fromisoformat("2026-09-19T12:00:00+07:00")
        one = datetime.fromisoformat("2026-09-19T13:00:00+07:00")

        def at(item: dict) -> datetime:
            return datetime.fromisoformat(item["at"])

        unknown = next(item for item in body["recent_funds_events"] if at(item) == noon)
        branch = next(item for item in body["recent_funds_events"] if at(item) == one)
        assert unknown["channel"] == "unknown"
        assert branch["channel"] == "branch"
        decided = datetime.fromisoformat(body["ts"])
        quiet = dict(body)
        quiet["recent_funds_events"] = [unknown]
        loud = dict(body)
        loud["recent_funds_events"] = [branch]
        row = {"new_payee": 1, "segment": "thường"}
        missed = evaluate(facts_from_row(quiet, row, 0.0, decided, None))
        kept = evaluate(facts_from_row(loud, row, 0.0, decided, None))
        assert "R_CALL_FUNDS_NEWPAYEE" not in missed.rules_fired
        assert missed.level < 2
        assert "R_CALL_FUNDS_NEWPAYEE" in kept.rules_fired
        assert kept.level == 2
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-ledger-channel",))
        with psycopg.connect(DATABASE) as conn:
            conn.execute("DELETE FROM bank.products WHERE id = ANY(%s)", (list(ids),))
            conn.commit()


def _movement(account: str, tx_ids: list[str]) -> int:
    with psycopg.connect(DATABASE) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN direction = 'in' THEN amount WHEN direction = 'out' THEN -amount ELSE 0 END), 0)
            FROM bank.entries
            WHERE account_id = %s AND tx_id = ANY(%s)
            """,
            (account, tx_ids),
        ).fetchone()
    return int(row[0])


def _entry_count(tx_id: str) -> int:
    with psycopg.connect(DATABASE) as conn:
        row = conn.execute("SELECT COUNT(*) FROM bank.entries WHERE tx_id = %s", (tx_id,)).fetchone()
    return int(row[0])


def test_level_1_waits_without_a_debit_until_confirm(bank):
    module, client, seen = bank
    original = module.engine_post
    before = _balance(SENDER)
    payee_before = _balance(PAYEE)

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        seen.append((path, body))
        if path == "/decide":
            return {
                "level": 1,
                "intervention": "banner",
                "funds_status": "released",
                "hold_until": None,
                "countdown_until": None,
                "actions": [],
                "reasons": [{"code": "call", "text": "Bạn đang trong cuộc gọi và chuyển tiền cho người nhận mới"}],
                "signals_present": ["call_active"],
                "rules_fired": ["R_CALL_NEWPAYEE"],
                "evidence_card": {
                    "signals": [],
                    "reasons_vi": ["Bạn đang trong cuộc gọi và chuyển tiền cho người nhận mới"],
                    "baseline_quality": "none",
                    "episode_id": "",
                },
            }
        if path.endswith("/choice"):
            return {"decision_id": path.split("/")[2], "choice": body.get("choice"), "logged": True}
        return {}

    module.engine_post = engine_post
    seen.clear()
    try:
        created = client.post(
            "/transfer",
            json={"from_account": SENDER, "to_account": PAYEE, "amount": 1000, "purpose_text": "pytest-level1-wait", "device": {"state": "none"}},
        )
        assert created.status_code == 200, created.text
        waiting = created.json()
        assert waiting["status"] == "awaiting_confirmation"
        assert waiting["funds_status"] == "held"
        assert waiting["decision"]["level"] == 1
        assert waiting["decision"]["intervention"] == "reminder"
        assert any(path == "/decide" for path, _body in seen)
        assert _balance(SENDER) == before
        assert _balance(PAYEE) == payee_before
        assert _entry_count(waiting["transfer_id"]) == 0
        assert _movement(SENDER, [waiting["transfer_id"]]) == 0
        spent_before = next(body["spent_today"] for path, body in seen if path == "/decide")

        confirmed = client.post(f"/transfers/{waiting['transfer_id']}/confirm", json={"response_ms": 800})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "done"
        assert confirmed.json()["funds_status"] == "released"
        assert _balance(SENDER) == before - 1000
        assert _balance(PAYEE) == payee_before + 1000
        assert _entry_count(waiting["transfer_id"]) == 2
        assert _balance(SENDER) - before == _movement(SENDER, [waiting["transfer_id"]])
        assert _balance(PAYEE) - payee_before == _movement(PAYEE, [waiting["transfer_id"]])
        assert any(path.endswith("/choice") and body.get("choice") == "continue" and body.get("response_ms") == 800 for path, body in seen)

        repeat = client.post(f"/transfers/{waiting['transfer_id']}/confirm", json={"response_ms": 1})
        assert repeat.status_code == 200, repeat.text
        assert _balance(SENDER) == before - 1000
        assert _balance(PAYEE) == payee_before + 1000

        seen.clear()
        other = client.post(
            "/transfer",
            json={"from_account": SENDER, "to_account": PAYEE, "amount": 2000, "purpose_text": "pytest-level1-cancel", "device": {"state": "none"}},
        )
        assert other.status_code == 200, other.text
        cancelled_id = other.json()["transfer_id"]
        assert other.json()["status"] == "awaiting_confirmation"
        assert next(body["spent_today"] for path, body in seen if path == "/decide") == spent_before + 1000
        assert _balance(SENDER) == before - 1000
        dropped = client.post(f"/transfers/{cancelled_id}/cancel", json={"response_ms": 100})
        assert dropped.status_code == 200, dropped.text
        assert dropped.json()["status"] == "cancelled"
        assert _balance(SENDER) == before - 1000
        assert _entry_count(cancelled_id) == 0
        assert _movement(SENDER, [cancelled_id]) == 0
        assert _balance(SENDER) - before == _movement(SENDER, [waiting["transfer_id"], cancelled_id])
        assert any(path.endswith("/choice") and body.get("choice") == "cancel" for path, body in seen)
        blocked = client.post(f"/transfers/{cancelled_id}/confirm", json={"response_ms": 0})
        assert blocked.status_code == 409
    finally:
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-level1-wait", "pytest-level1-cancel"))
        _set_balance(PAYEE, payee_before)


def test_level_1_expires_after_ten_minutes_and_cancel_frees_the_key(bank):
    module, client, seen = bank
    original = module.engine_post
    before = _balance(SENDER)
    clock = module.CLOCK
    anchor = clock.now()
    was_paused = clock.paused
    feed = clock.feed
    expire_key = "pytest-level1-expire"
    reuse_key = "pytest-level1-reuse"

    def engine_post(path: str, body: dict, timeout: float = 30) -> dict:
        seen.append((path, body))
        if path == "/decide":
            return {
                "level": 1,
                "intervention": "banner",
                "funds_status": "released",
                "hold_until": None,
                "countdown_until": None,
                "actions": [],
                "reasons": [{"code": "call", "text": "Bạn đang trong cuộc gọi và chuyển tiền cho người nhận mới"}],
                "signals_present": ["call_active"],
                "rules_fired": ["R_CALL_NEWPAYEE"],
                "evidence_card": {
                    "signals": [],
                    "reasons_vi": ["Bạn đang trong cuộc gọi và chuyển tiền cho người nhận mới"],
                    "baseline_quality": "none",
                    "episode_id": "",
                },
            }
        if path.endswith("/choice"):
            return {"decision_id": path.split("/")[2], "choice": body.get("choice"), "logged": True}
        return {}

    def post(key: str, purpose: str, amount: int = 1500):
        return client.post(
            "/transfer",
            headers={"Idempotency-Key": key},
            json={"from_account": SENDER, "to_account": PAYEE, "amount": amount, "purpose_text": purpose, "device": {"state": "none"}},
        )

    module.engine_post = engine_post
    seen.clear()
    clock.pause()
    try:
        created = post(expire_key, "pytest-level1-expire")
        assert created.status_code == 200, created.text
        waiting = created.json()
        first_id = waiting["transfer_id"]
        assert waiting["status"] == "awaiting_confirmation"
        spent_before = next(body["spent_today"] for path, body in seen if path == "/decide")
        clock.advance(600)
        held = post(expire_key, "pytest-level1-expire")
        assert held.status_code == 200, held.text
        assert held.json()["transfer_id"] == first_id
        assert held.json()["status"] == "awaiting_confirmation"
        clock.advance(1)
        renewed = post(expire_key, "pytest-level1-expire")
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["transfer_id"] != first_id
        assert renewed.json()["status"] == "awaiting_confirmation"
        assert _balance(SENDER) == before
        assert _entry_count(first_id) == 0
        abandoned = [body for path, body in seen if str(path).endswith("/choice") and body.get("choice") == "abandoned"]
        assert abandoned == [{"choice": "abandoned", "response_ms": 0}]
        assert next(body["spent_today"] for path, body in reversed(seen) if path == "/decide") == spent_before
        blocked = client.post(f"/transfers/{first_id}/confirm", json={"response_ms": 900})
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "Lệnh đã hết hạn, vui lòng thực hiện lại"
        assert [body for path, body in seen if str(path).endswith("/choice") and body.get("choice") == "abandoned"] == abandoned
        assert _balance(SENDER) == before

        seen.clear()
        clock.advance(1)
        opened = post(reuse_key, "pytest-level1-reuse", 2500)
        assert opened.status_code == 200, opened.text
        opened_id = opened.json()["transfer_id"]
        assert opened.json()["status"] == "awaiting_confirmation"
        dropped = client.post(f"/transfers/{opened_id}/cancel", json={"response_ms": 40})
        assert dropped.status_code == 200, dropped.text
        assert dropped.json()["status"] == "cancelled"
        assert any(str(path).endswith("/choice") and body.get("choice") == "cancel" and body.get("response_ms") == 40 for path, body in seen)
        assert not any(body.get("choice") == "abandoned" for _, body in seen)
        again = post(reuse_key, "pytest-level1-reuse", 2500)
        assert again.status_code == 200, again.text
        assert again.json()["transfer_id"] != opened_id
        assert again.json()["status"] == "awaiting_confirmation"
        assert _balance(SENDER) == before
        assert _entry_count(opened_id) == 0
        assert next(body["spent_today"] for path, body in seen if path == "/decide") == spent_before
    finally:
        clock.seek(anchor)
        clock.paused = was_paused
        clock.feed = feed
        clock._real = time.monotonic()
        module.engine_post = original
        _restore_transfer(SENDER, before, ("pytest-level1-expire", "pytest-level1-reuse"))
        with psycopg.connect(DATABASE) as conn:
            conn.execute("DELETE FROM bank.idempotency WHERE key = ANY(%s)", ([expire_key, reuse_key],))
            conn.commit()
