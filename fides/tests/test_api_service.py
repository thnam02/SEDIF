"""API v2.1: parity với chính sách offline, who_guides, profile basic, độ trễ."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("FIDES_AUDIT", str(ROOT / "out" / "audit" / "test_decisions.jsonl"))
os.environ.setdefault("FIDES_HMAC_KEY", "test-hmac")

REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.policy import LEVELS, Respond, apply_respond, raise_level
from features.store import _DEVICE_SIGNALS, _TELCO_SIGNALS

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def client():
    path = Path(os.environ["FIDES_AUDIT"])
    if path.exists():
        path.unlink()
    from api.service import app

    with TestClient(app) as handle:
        yield handle


def _sample(client: TestClient, size: int = 2000) -> pd.DataFrame:
    features = pd.read_parquet(ROOT / "out" / "features_single_bank_test.parquet", columns=["session_id", "amount"])
    features["session_id"] = features["session_id"].astype(str)
    sessions = pd.read_parquet(
        SIM / "out" / "observed" / "sessions.parquet",
        columns=["session_id", "account_id", "end_ts"],
    )
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    frame = features.merge(sessions, on="session_id", how="inner")
    rng = np.random.default_rng(42)
    chosen = rng.choice(len(frame), size=min(size, len(frame)), replace=False)
    return frame.iloc[chosen].reset_index(drop=True)


def _payload(row, profile: str = "advanced", device: bool = True) -> dict:
    as_of = pd.Timestamp(row.end_ts) + pd.Timedelta(microseconds=1)
    body = {
        "profile": profile,
        "sender_account": str(row.account_id),
        "payee_account": "A0000000",
        "amount": 0 if pd.isna(row.amount) else float(row.amount),
        "ts": as_of.isoformat(),
    }
    if device:
        body["device"] = {
            "in_call": False,
            "call_seconds_since_open": 0,
            "call_direction": "inbound",
            "call_ended_in_session": False,
            "acct_input": "typed",
            "app_switches": 0,
        }
    return body


def test_who_guides_never_lowers_level() -> None:
    for level in LEVELS:
        assert raise_level(level, None) == level
        assert raise_level(level, False) == level
        assert raise_level(level, "không") == level
        raised = raise_level(level, "có người hướng dẫn")
        assert LEVELS.index(raised) >= LEVELS.index(level)
    record = {
        "level": "high",
        "segment": "thường",
        "has_guardian": False,
        "guardian_confirmed": False,
        "status": "open",
        "decided_at": time.time(),
        "actions": ["hangup_required", "recovery_window"],
    }
    apply_respond(record, Respond(decision_id="x", who_guides_answer="không"), time.time())
    assert record["level"] == "high"
    apply_respond(record, Respond(decision_id="x", who_guides_answer="có"), time.time())
    assert record["level"] == "high"


def test_guardian_gate_and_cancel() -> None:
    now = time.time()
    record = {
        "level": "medium",
        "segment": "cao tuổi",
        "has_guardian": True,
        "guardian_confirmed": False,
        "status": "waiting_guardian",
        "decided_at": now,
        "actions": ["pause_and_verify", "guardian_confirm"],
    }
    apply_respond(record, Respond(decision_id="x", guardian_confirm=False), now)
    assert record["status"] == "waiting_guardian"
    apply_respond(record, Respond(decision_id="x", guardian_confirm=True), now)
    assert record["status"] == "released"
    apply_respond(record, Respond(decision_id="x", cancel=True), now)
    assert record["status"] == "cancelled"
    late = dict(record)
    late["decided_at"] = now - 48 * 3600
    late["status"] = "released"
    apply_respond(late, Respond(decision_id="x", cancel=True), now)
    assert late["status"] != "cancelled"


def test_missing_device_forces_basic(client: TestClient) -> None:
    row = _sample(client, 1).iloc[0]
    response = client.post("/decide", json=_payload(row, profile="standard", device=False))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] == "basic"
    missing = set(body["missing_signals"])
    for name in (*_DEVICE_SIGNALS, *_TELCO_SIGNALS):
        assert name in missing


def _offline_levels(runtime, sample: pd.DataFrame) -> list[str]:
    """Chính sách đã đóng gói trên vector feature của parquet test, không đi qua API."""
    from features.service import COST_ETA, levels_from_counts

    columns = runtime.columns
    table = pd.read_parquet(ROOT / "out" / "features_single_bank_test.parquet", columns=["session_id", *columns])
    table["session_id"] = table["session_id"].astype(str)
    table = table.drop_duplicates("session_id").set_index("session_id").loc[sample["session_id"].tolist()]
    matrix = table[columns].copy()
    matrix["add_method"] = matrix["add_method"].astype("string").astype(runtime.categories)
    for name in columns:
        if name != "add_method":
            matrix[name] = pd.to_numeric(matrix[name], errors="coerce")
    raw = runtime.model.predict(matrix)
    probability = np.array([runtime._calibrate(float(item)) for item in raw])
    sessions = pd.read_parquet(SIM / "out" / "observed" / "sessions.parquet", columns=["session_id", "customer_id"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["customer_id"] = sessions["customer_id"].astype(str)
    customer = dict(zip(sessions["session_id"], sessions["customer_id"]))
    amounts = pd.to_numeric(table["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
    families = np.zeros(len(sample), dtype=int)
    for index, session_id in enumerate(sample["session_id"].tolist()):
        as_of = pd.Timestamp(sample["end_ts"].iloc[index]) + pd.Timedelta(microseconds=1)
        behavior = runtime.store.behavior_at(session_id, as_of, "single_bank")
        frame = pd.DataFrame([{name: behavior.get(name) for name in ("new_payees_7d", "new_payees_30d", "cusum_out_30d", "amount_rise_run", "balance_out_ratio_30d")}])
        from eval.decision import _long_matrix, apply_isolation_forest

        query, query_cusum = _long_matrix(frame, runtime.g3_fill)
        forest = apply_isolation_forest(runtime.g3_forest, runtime.g3_norm, query)
        cdf = np.searchsorted(runtime.g3_cusum_sorted, query_cusum, side="right") / max(len(runtime.g3_cusum_sorted), 1)
        anomaly = float(0.5 * (forest + cdf)[0]) >= float(runtime.policy["g3_cutoff"])
        call, entry, payee, funding = runtime.family_rows.get(session_id, (False, False, False, False))
        families[index] = int(call) + int(entry) + int(payee) + int(funding) + int(anomaly)
    scale = np.ones(len(sample), dtype=float)
    for index, session_id in enumerate(sample["session_id"].tolist()):
        segment = runtime.segment_of.get(customer.get(session_id, ""), "")
        scale[index] = float(runtime.policy["c_fp_scale"].get(segment, 1.0))
    levels = levels_from_counts(probability, amounts, families, COST_ETA, scale, float(runtime.policy["tau_hi"]))
    return [str(item) for item in levels]


def test_parity_and_latency(client: TestClient) -> None:
    sample = _sample(client, 2000)
    expected = _offline_levels(client.app.state.runtime, sample)
    elapsed = []
    mismatches = []
    for row, truth in zip(sample.itertuples(index=False), expected):
        started = time.perf_counter()
        response = client.post("/decide", json=_payload(row))
        elapsed.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        got = response.json()["level"]
        if got != truth:
            mismatches.append((row.session_id, truth, got))
    assert not mismatches, mismatches[:5]
    limit = float(np.percentile(elapsed, 95))
    assert limit < 0.2, f"p95 {limit:.3f}s"


def test_audit_has_hash_not_raw_account(client: TestClient) -> None:
    row = _sample(client, 1).iloc[0]
    response = client.post("/decide", json=_payload(row))
    assert response.status_code == 200
    decision_id = response.json()["decision_id"]
    record = client.app.state.audit.records[decision_id]
    assert row.account_id not in json_dumps(record)
    assert "sender_hash" in record
    follow = client.post("/respond", json={"decision_id": decision_id, "who_guides_answer": "không"})
    assert follow.status_code == 200
    assert follow.json()["level"] == response.json()["level"]


def json_dumps(record: dict) -> str:
    import json

    return json.dumps(record, ensure_ascii=False)
