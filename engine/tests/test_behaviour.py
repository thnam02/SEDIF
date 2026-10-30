"""Tầng thao tác v2.4. Redis db 14. Không flush db 15."""

from __future__ import annotations

import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import redis

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO)]

from engine.features.behaviour import (
    CONSISTENCY,
    EPS,
    SCORED,
    assess,
    count_score,
    empirical_p,
    key_for,
    load_reference,
    quality_name,
    remember,
    robust_z,
    score_features,
)
from engine.policy.v24 import Facts, _baseline, evaluate, load_policy
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
ICT_HOUR = datetime(2026, 9, 21, 9, 0, tzinfo=ICT)


def _facts(**overrides) -> Facts:
    base = dict(
        sender="a" * 64,
        payee="b" * 64,
        amount=1_000_000,
        ts=ICT_HOUR,
        call_state="none",
        task_type="saved_payee",
        transfer_purpose="purchase",
        device_state="none",
        funds_events=[],
        new_payee=False,
        payee_age_days=800.0,
        distinct_senders_7d=1.0,
        passthrough_24h=0.1,
        simo_listed=False,
        prior_payments=0,
        first_days_before_listing=0.0,
        p_fraud=0.1,
        behaviour_p_rarity=None,
        same_task_sessions=None,
        limit_per_txn=500_000_000,
        limit_per_day=2_000_000_000,
    )
    base.update(overrides)
    return Facts(**base)
REDIS = "redis://localhost:6379/14"
USER = "beh-user"


def _flat(value: float) -> dict:
    return {name: float(value) for name in SCORED} | {"session_duration_ms": 1000.0}


def _pop(median: float, mad: float) -> dict:
    return {name: {"median": median, "mad": mad} for name in SCORED}


def _up() -> dict:
    return {name: "up" for name in SCORED}


def _client():
    client = redis.Redis.from_url(REDIS, decode_responses=True)
    client.delete(key_for(USER, "saved_payee"))
    client.delete(key_for(USER, "new_typed"))
    return client


def _z(value: float, sessions: int, personal_median: float, personal_mad: float, pop_median: float, pop_mad: float) -> float:
    weight = sessions / (sessions + 5) if sessions else 0.0
    center = weight * personal_median + (1.0 - weight) * pop_median
    shrunk = weight * personal_mad + (1.0 - weight) * pop_mad
    used = max(shrunk, 0.5 * pop_mad)
    return (value - center) / (CONSISTENCY * used + EPS)


def test_shrinkage_with_zero_three_and_eight_sessions():
    population = _pop(100.0, 20.0)
    observed = _flat(100.0)
    base3 = [_flat(40.0) for _ in range(3)]
    base8 = [_flat(40.0) for _ in range(8)]
    z0 = score_features(observed, [], population, _up()).feature_scores["review_dwell_ms"]
    z3 = score_features(observed, base3, population, _up()).feature_scores["review_dwell_ms"]
    z8 = score_features(observed, base8, population, _up()).feature_scores["review_dwell_ms"]
    assert z0 == 0.0
    assert z3 == _z(100.0, 3, 40.0, 0.0, 100.0, 20.0)
    assert z8 == _z(100.0, 8, 40.0, 0.0, 100.0, 20.0)
    assert z3 < z8


def test_mad_floor_keeps_identical_sessions_finite():
    population = _pop(100.0, 10.0)
    baseline = [_flat(100.0) for _ in range(8)]
    scored = score_features(_flat(130.0), baseline, population, _up())
    weight = 8 / 13
    shrunk = (1.0 - weight) * 10.0
    assert shrunk < 5.0
    floored = (130.0 - 100.0) / (CONSISTENCY * 5.0 + EPS)
    unfloored = (130.0 - 100.0) / (CONSISTENCY * shrunk + EPS)
    assert math.isfinite(scored.feature_scores["median_gap_ms"])
    assert scored.feature_scores["median_gap_ms"] == floored
    assert scored.feature_scores["median_gap_ms"] < unfloored
    zero = _pop(100.0, 0.0)
    bounded = score_features(_flat(130.0), baseline, zero, _up())
    assert math.isfinite(bounded.score)


def test_one_sided_scores_only_the_configured_direction():
    population = _pop(100.0, 10.0)
    up = score_features(_flat(70.0), [], population, _up()).feature_scores["median_gap_ms"]
    assert up == 0.0
    higher = score_features(_flat(130.0), [], population, _up()).feature_scores["median_gap_ms"]
    assert higher > 0
    down = dict(_up())
    down["median_gap_ms"] = "down"
    below = score_features(_flat(70.0), [], population, down).feature_scores["median_gap_ms"]
    above = score_features(_flat(130.0), [], population, down).feature_scores["median_gap_ms"]
    assert below > 0
    assert above == 0.0


def test_count_score_rises_when_the_value_is_rarer():
    typical = count_score(0, [0, 0, 0, 0, 0, 0])
    rare = count_score(2, [0, 0, 0, 0, 0, 0])
    assert typical == 0.0
    assert rare > typical


def test_quality_bands_and_gating():
    policy = load_policy()
    assert policy["behavioural_baseline"]["min_sessions_for_level_2"] == 3
    assert policy["behavioural_baseline"]["min_quality"] == "medium"
    assert policy["behavioural_baseline"]["mad_floor_fraction_of_population"] == 0.5
    assert policy["thresholds"]["behaviour_p_rarity_at_most"]["demo"] is True
    assert _baseline(0) == "none"
    assert _baseline(2) == "low"
    assert _baseline(3) == "medium"
    assert _baseline(7) == "medium"
    assert _baseline(8) == "high"
    assert quality_name(0) == "none"
    assert quality_name(8) == "high"
    low = evaluate(
        _facts(
            call_state="active",
            new_payee=True,
            behaviour_p_rarity=0.01,
            same_task_sessions=2,
            p_fraud=0.0,
            funds_events=[],
        )
    )
    rare = next(item for item in low.signals if item.signal_id == "behaviour_rare")
    assert rare.status == "unknown"
    assert "R_BEHAVIOUR_GUIDED" not in low.rules_fired
    assert low.level < 2
    guided = evaluate(
        _facts(
            call_state="active",
            new_payee=True,
            behaviour_p_rarity=0.01,
            same_task_sessions=3,
            p_fraud=0.0,
            funds_events=[],
        )
    )
    assert "R_BEHAVIOUR_GUIDED" in guided.rules_fired
    assert guided.level == 2
    simo = evaluate(
        _facts(
            simo_listed=True,
            prior_payments=0,
            first_days_before_listing=0.0,
            behaviour_p_rarity=0.01,
            same_task_sessions=1,
            p_fraud=0.0,
            call_state="none",
        )
    )
    assert "R_SIMO" not in simo.rules_fired
    assert simo.level < 2
    simo_ready = evaluate(
        _facts(
            simo_listed=True,
            prior_payments=0,
            first_days_before_listing=0.0,
            behaviour_p_rarity=0.01,
            same_task_sessions=3,
            p_fraud=0.0,
            call_state="none",
        )
    )
    assert "R_SIMO" in simo_ready.rules_fired


def test_evidence_card_keeps_the_score_when_the_baseline_is_low():
    class Card:
        score = 1.25
        p_rarity = 0.01
        reasons = ["dừng lâu hơn thường lệ", "rời app 2 lần"]

    out = evaluate(
        _facts(
            behaviour_p_rarity=None,
            same_task_sessions=1,
            p_fraud=0.0,
            call_state="none",
        ),
        behaviour=Card(),
    )
    card = out.evidence_card
    assert card["behaviour_score"] == 1.25
    assert card["p_rarity"] == 0.01
    assert card["baseline_quality"] == "low"
    shown = " ".join(card["reasons_vi"])
    assert "dừng lâu hơn thường lệ" not in shown
    assert "rời app 2 lần" not in shown
    rare = next(item for item in out.signals if item.signal_id == "behaviour_rare")
    assert rare.status == "unknown"
    ready = evaluate(
        _facts(
            call_state="active",
            new_payee=True,
            behaviour_p_rarity=0.01,
            same_task_sessions=10,
            p_fraud=0.0,
            funds_events=[],
        ),
        behaviour=Card(),
    )
    assert ready.baseline_quality == "high"
    assert "dừng lâu hơn thường lệ" in ready.reasons_vi


def test_chronological_baseline_and_level_gate():
    client = _client()
    try:
        first = assess(client, USER, "saved_payee", _flat(100), ICT_HOUR, experiment=True)
        assert first is not None and first.n == 0
        remember(client, first, 0)
        same = assess(client, USER, "saved_payee", _flat(100), ICT_HOUR, experiment=True)
        assert same.n == 0
        later = assess(client, USER, "saved_payee", _flat(110), ICT_HOUR + timedelta(hours=1), experiment=True)
        assert later.n == 1
        future = assess(client, USER, "saved_payee", _flat(140), ICT_HOUR + timedelta(hours=3), experiment=True)
        remember(client, future, 0)
        middle = assess(client, USER, "saved_payee", _flat(120), ICT_HOUR + timedelta(hours=2), experiment=True)
        assert middle.n == 1
        blocked = assess(client, USER, "new_typed", _flat(100), ICT_HOUR, experiment=True)
        remember(client, blocked, 2)
        after = assess(client, USER, "new_typed", _flat(100), ICT_HOUR + timedelta(hours=1), experiment=True)
        assert after.n == 0
        kept = assess(client, USER, "new_typed", _flat(100), ICT_HOUR + timedelta(hours=2), experiment=True)
        remember(client, kept, 1)
        seen = assess(client, USER, "new_typed", _flat(100), ICT_HOUR + timedelta(hours=3), experiment=True)
        assert seen.n == 1
    finally:
        client.delete(key_for(USER, "saved_payee"))
        client.delete(key_for(USER, "new_typed"))


def test_production_delay_hides_a_session_for_24_hours():
    client = _client()
    try:
        view = assess(client, USER, "saved_payee", _flat(100), ICT_HOUR, experiment=False)
        remember(client, view, 0)
        soon = assess(client, USER, "saved_payee", _flat(100), ICT_HOUR + timedelta(hours=23), experiment=False)
        assert soon.n == 0
        ready = assess(client, USER, "saved_payee", _flat(100), ICT_HOUR + timedelta(hours=25), experiment=False)
        assert ready.n == 1
    finally:
        client.delete(key_for(USER, "saved_payee"))


def test_shipped_reference_is_labelled_synthetic():
    loaded = load_reference("saved_payee")
    assert loaded is not None
    assert loaded["provenance"] == "synthetic, demo only"
    assert len(loaded["scores"]) >= 100
    assert loaded["baseline_n"]
    assert len(loaded["baseline_n"]) == len(loaded["scores"])
    assert loaded["population"]["background_total_ms"]["mad"] > 0
    assert empirical_p(max(loaded["scores"]) + 1, loaded["scores"]) < empirical_p(min(loaded["scores"]), loaded["scores"])


def test_one_sided_helper_matches_zero_when_the_shift_is_the_other_way():
    assert robust_z(80, [100, 100, 100], 100, 10, 5, 0.5, "up") == 0.0


def test_demo_reset_clears_behaviour_keys_only():
    script = (REPO / "scripts" / "demo_reset.sh").read_text(encoding="utf-8")
    start = script.index("# BEGIN purge_prefix")
    end = script.index("# END purge_prefix") + len("# END purge_prefix")
    function = script[start:end]
    assert "MATCH \"$pattern\"" in function
    assert "FLUSHDB" not in function
    assert "beh:v24:*" in script
    client = redis.Redis.from_url(REDIS, decode_responses=True)
    client.set("beh:v24:purge-test:saved_payee", "1")
    client.set("pol:v24:purge-test", "1")
    wrapper = "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            "REDIS_HOST=127.0.0.1",
            "REDIS_PORT=6379",
            "DB=14",
            function,
            "purge_prefix 'beh:v24:*' 'beh:v24'",
            "",
        ]
    )
    done = subprocess.run(["/bin/sh", "-c", wrapper], check=False, capture_output=True, text=True)
    try:
        assert done.returncode == 0, done.stderr
        assert client.get("beh:v24:purge-test:saved_payee") is None
        assert client.get("pol:v24:purge-test") == "1"
        assert "beh:v24 removed" in done.stdout
    finally:
        client.delete("pol:v24:purge-test")
        client.delete("beh:v24:purge-test:saved_payee")
