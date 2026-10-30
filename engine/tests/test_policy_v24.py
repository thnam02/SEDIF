"""Luật /decide v2.4. Không đụng model, không flush Redis."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO)]

from engine.policy.v24 import CALL_NEWPAYEE_TEXT, Facts, MemoryState, cost_tau, evaluate, load_policy

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
SENDER = "a" * 64
PAYEE = "b" * 64


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 21, hour, minute, tzinfo=ICT)


def _facts(**overrides) -> Facts:
    base = dict(
        sender=SENDER,
        payee=PAYEE,
        amount=1_000_000,
        ts=_at(9),
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


def _funds(when: datetime, kind: str = "term_deposit_closure") -> list[dict]:
    return [{"type": kind, "at": when.isoformat(), "channel": "app"}]


def _ba_man(ts: datetime, call: str = "active", payee: str = PAYEE) -> Facts:
    return _facts(
        ts=ts,
        payee=payee,
        call_state=call,
        task_type="new_typed",
        new_payee=True,
        funds_events=_funds(ts - timedelta(hours=2)),
        payee_age_days=None,
        distinct_senders_7d=None,
        passthrough_24h=None,
    )


def test_policy_file_records_provenance():
    policy = load_policy()
    assert policy["version"] == "2.4.0"
    for name, item in policy["thresholds"].items():
        assert item["provenance"] == "chosen on simulated data", name
    assert policy["thresholds"]["model_p_high"]["value"] == 0.8125


def _signal(out, name: str):
    return next(item for item in out.signals if item.signal_id == name)


def test_customer_lines_keep_context_on_the_card_without_raising_the_level():
    pasted = evaluate(_facts(task_type="new_pasted", call_state="none", p_fraud=0.0, new_payee=False, funds_events=[]))
    assert pasted.level == 0
    assert pasted.reasons_vi == []
    assert _signal(pasted, "entry_paste_or_qr").status == "present"
    scanned = evaluate(_facts(task_type="qr", call_state="active", p_fraud=0.0, new_payee=False, funds_events=[]))
    assert scanned.level == 0
    assert "Quét mã QR." not in scanned.reasons_vi
    assert _signal(scanned, "entry_paste_or_qr").status == "present"
    assert _signal(scanned, "has_call").status == "present"
    costly = evaluate(_facts(amount=120_000_000, p_fraud=0.5, call_state="none", task_type="saved_payee", new_payee=False, funds_events=[]))
    assert "Số tiền lớn và giao dịch khác thường so với lịch sử" in costly.reasons_vi
    assert "Điểm mô hình vượt ngưỡng theo số tiền của lệnh này." not in costly.reasons_vi


def test_call_and_new_payee_is_level_1_and_other_context_stays_at_0(caplog):
    quiet = dict(p_fraud=0.0, funds_events=[], task_type="new_typed")
    alone = evaluate(_facts(new_payee=True, call_state="none", **quiet))
    assert alone.level == 0
    assert "R_CALL_NEWPAYEE" not in alone.rules_fired
    assert _signal(alone, "beneficiary_is_new").status == "present"
    pasted = evaluate(_facts(new_payee=True, call_state="none", task_type="new_pasted", p_fraud=0.0, funds_events=[]))
    assert pasted.level == 0
    assert _signal(pasted, "entry_paste_or_qr").status == "present"
    assert _signal(pasted, "beneficiary_is_new").status == "present"
    saved = evaluate(_facts(new_payee=False, call_state="active", task_type="saved_payee", p_fraud=0.0, funds_events=[]))
    assert saved.level == 0
    assert _signal(saved, "has_call").status == "present"
    called = evaluate(_facts(new_payee=True, call_state="active", **quiet))
    assert called.level == 1
    assert called.rules_fired == ["R_CALL_NEWPAYEE"]
    assert called.reasons_vi == [CALL_NEWPAYEE_TEXT]
    ended = evaluate(_facts(new_payee=True, call_state="ended_in_session", **quiet))
    assert ended.level == 1
    assert "R_CALL_NEWPAYEE" in ended.rules_fired
    assert ended.reasons_vi == [CALL_NEWPAYEE_TEXT]
    caplog.set_level(logging.INFO, logger="engine.policy")
    unknown = evaluate(_facts(new_payee=True, call_state="unknown", **quiet))
    assert unknown.level == 0
    assert "R_CALL_NEWPAYEE" not in unknown.rules_fired
    assert any("R_CALL_NEWPAYEE" in record.message and "unknown" in record.message for record in caplog.records)


def test_call_and_qr_to_known_shop_stays_at_most_banner():
    out = evaluate(
        _facts(
            call_state="active",
            task_type="qr",
            new_payee=False,
            funds_events=[],
            transfer_purpose="purchase",
        )
    )
    assert out.level <= 1
    assert out.intervention in {"none", "banner"}
    assert "R_CALL_FUNDS_NEWPAYEE" not in out.rules_fired


def test_ba_man_active_call_closure_and_new_payee():
    out = evaluate(_ba_man(_at(9)))
    text = " ".join(out.reasons_vi).lower()
    assert out.level == 2
    assert out.intervention == "three_button"
    assert "R_CALL_FUNDS_NEWPAYEE" in out.rules_fired
    assert "cuộc gọi" in text
    assert "tất toán" in text
    assert "người nhận" in text


def test_call_ended_in_session_still_level_2_and_unknown_does_not_fire(caplog):
    ended = evaluate(_ba_man(_at(9), call="ended_in_session"))
    assert ended.level == 2
    assert "R_CALL_FUNDS_NEWPAYEE" in ended.rules_fired
    caplog.set_level(logging.INFO, logger="engine.policy")
    unknown = evaluate(_ba_man(_at(10), call="unknown", payee="c" * 64))
    assert "R_CALL_FUNDS_NEWPAYEE" not in unknown.rules_fired
    assert unknown.level < 2
    assert any("unknown" in record.message for record in caplog.records)


def test_simo_info_rule_does_not_lower_a_verified_mule():
    known = evaluate(
        _facts(
            simo_listed=True,
            prior_payments=4,
            first_days_before_listing=90,
            new_payee=False,
            call_state="none",
        )
    )
    assert known.level == 1
    assert "R_SIMO_KNOWN" in known.rules_fired
    assert "R_SIMO" not in known.rules_fired
    plain = evaluate(_facts(simo_listed=True, prior_payments=0, first_days_before_listing=0, p_fraud=0.0))
    assert plain.level == 1
    assert "R_SIMO_ALONE" in plain.rules_fired
    assert "R_SIMO" not in plain.rules_fired
    mule = evaluate(
        _facts(
            simo_listed=True,
            prior_payments=4,
            first_days_before_listing=90,
            verified=[
                {
                    "signal_id": "recipient_bank_confirmed_mule",
                    "status": "present",
                    "source": "core_banking",
                    "active": True,
                }
            ],
        )
    )
    assert mule.level == 3
    assert mule.intervention == "security_workflow"
    assert "R_VERIFIED_RECIPIENT" in mule.rules_fired
    assert "R_SIMO_KNOWN" in mule.rules_fired


def test_simo_with_active_call_is_level_2():
    out = evaluate(
        _facts(
            simo_listed=True,
            prior_payments=0,
            first_days_before_listing=0,
            call_state="active",
            p_fraud=0.0,
        )
    )
    assert out.level == 2
    assert "R_SIMO" in out.rules_fired
    assert "R_SIMO_ALONE" not in out.rules_fired
    assert "R_SIMO_KNOWN" not in out.rules_fired


def test_merchant_with_many_senders_and_no_passthrough_is_not_unusual():
    blocked = evaluate(
        _facts(
            new_payee=True,
            task_type="new_typed",
            distinct_senders_7d=20,
            passthrough_24h=0.1,
            payee_age_days=800,
            merchant_verified=True,
            p_fraud=0.0,
            call_state="none",
        )
    )
    assert "R_RECIPIENT_UNUSUAL" not in blocked.rules_fired
    assert blocked.level < 2
    senders = evaluate(
        _facts(
            new_payee=True,
            task_type="new_typed",
            distinct_senders_7d=7,
            passthrough_24h=0.96,
            payee_age_days=800,
            merchant_verified=False,
            p_fraud=0.0,
            call_state="none",
            funds_events=[],
            transfer_purpose="purchase",
            device_state="none",
        )
    )
    assert senders.level == 2
    assert "R_RECIPIENT_UNUSUAL" in senders.rules_fired
    young = evaluate(
        _facts(
            new_payee=True,
            task_type="new_typed",
            distinct_senders_7d=1,
            passthrough_24h=0.96,
            payee_age_days=10,
            merchant_verified=False,
            p_fraud=0.0,
            call_state="none",
            funds_events=[],
        )
    )
    assert young.level == 2
    assert "R_RECIPIENT_UNUSUAL" in young.rules_fired


def test_recipient_only_holds_the_same_payee_and_not_the_next():
    state = MemoryState()
    sender = "7" * 64
    first_payee = "8" * 64
    opened = evaluate(
        _facts(
            sender=sender,
            payee=first_payee,
            ts=_at(9),
            new_payee=True,
            task_type="new_typed",
            passthrough_24h=0.96,
            distinct_senders_7d=7,
            payee_age_days=800,
            merchant_verified=False,
            call_state="none",
            funds_events=[],
            transfer_purpose="purchase",
            device_state="none",
            p_fraud=0.0,
        ),
        state,
    )
    assert opened.level == 2
    assert "R_RECIPIENT_UNUSUAL" in opened.rules_fired
    same = evaluate(
        _facts(
            sender=sender,
            payee=first_payee,
            ts=_at(10),
            new_payee=False,
            task_type="saved_payee",
            call_state="none",
            funds_events=[],
            transfer_purpose="purchase",
            p_fraud=0.0,
            passthrough_24h=0.1,
            distinct_senders_7d=1,
            payee_age_days=800,
            merchant_verified=False,
        ),
        state,
    )
    assert same.level == 2
    assert same.episode_id == opened.episode_id
    other = evaluate(
        _facts(
            sender=sender,
            payee="6" * 64,
            ts=_at(11),
            new_payee=True,
            task_type="new_typed",
            call_state="none",
            funds_events=[],
            transfer_purpose="purchase",
            p_fraud=0.0,
            passthrough_24h=0.1,
            distinct_senders_7d=1,
            payee_age_days=800,
            merchant_verified=False,
        ),
        state,
    )
    assert other.level < 2
    assert other.episode_id == ""


def test_simo_unknown_history_is_not_treated_as_absent():
    out = evaluate(_facts(simo_listed=True, prior_payments=None, first_days_before_listing=None))
    assert "R_SIMO" not in out.rules_fired
    assert "R_SIMO_KNOWN" not in out.rules_fired


def test_near_limit_sums_splits_and_ignores_ordinary_amounts():
    state = MemoryState()
    sender = "d" * 64
    last = None
    for index in range(3):
        last = evaluate(
            _facts(
                sender=sender,
                payee=f"{index:064x}",
                amount=499_999_999,
                ts=_at(9, index * 10),
                limit_per_txn=500_000_000,
                new_payee=False,
                call_state="none",
            ),
            state,
        )
        assert last.split_suspected
    assert last is not None
    assert last.cumulative_amount == 3 * 499_999_999
    near_bio = evaluate(_facts(amount=9_990_000, limit_per_txn=None, limit_per_day=None, sender="e" * 64))
    assert near_bio.split_suspected
    ordinary = evaluate(_facts(amount=5_000_000, limit_per_txn=None, limit_per_day=None, sender="f" * 64))
    assert not ordinary.split_suspected


def test_episode_skips_saved_payee_and_floors_the_next_new_payee():
    state = MemoryState()
    sender = "1" * 64
    opened_facts = _ba_man(_at(9), payee="2" * 64)
    opened_facts.sender = sender
    opened = evaluate(opened_facts, state)
    assert opened.level == 2
    bill = evaluate(
        _facts(
            sender=sender,
            payee="3" * 64,
            ts=_at(11),
            task_type="saved_payee",
            new_payee=False,
            call_state="none",
            funds_events=[],
            transfer_purpose="purchase",
            amount=350_000,
        ),
        state,
    )
    assert bill.level < 2
    assert bill.episode_id == ""
    nxt = evaluate(
        _facts(
            sender=sender,
            payee="4" * 64,
            ts=_at(11, 5),
            new_payee=True,
            call_state="none",
            funds_events=[],
            task_type="new_typed",
            p_fraud=0.1,
        ),
        state,
    )
    assert nxt.level == 2
    assert nxt.episode_id == opened.episode_id
    assert nxt.episode_id != ""


def test_model_cost_is_level_1_until_tau_hi():
    amount = 50_000_000
    tau = cost_tau(amount, "thường")
    sme = cost_tau(amount, "doanh nghiệp nhỏ")
    assert abs(tau - (150_000 / (150_000 + amount))) < 1e-12
    assert abs(sme - (450_000 / (450_000 + amount))) < 1e-12
    assert tau < sme < 0.8125
    quiet = dict(
        amount=amount,
        call_state="none",
        new_payee=False,
        funds_events=[],
        task_type="saved_payee",
        transfer_purpose="purchase",
        device_state="none",
        simo_listed=False,
        segment="thường",
    )
    just = evaluate(_facts(**quiet, p_fraud=tau + 1e-6))
    assert just.level == 1
    assert "R_MODEL_COST" in just.rules_fired
    assert "R_MODEL_HIGH" not in just.rules_fired
    sme_quiet = dict(quiet)
    sme_quiet["segment"] = "doanh nghiệp nhỏ"
    below_sme = evaluate(_facts(**sme_quiet, p_fraud=tau + 1e-6))
    assert below_sme.level == 0
    assert "R_MODEL_COST" not in below_sme.rules_fired
    high = evaluate(_facts(**quiet, p_fraud=0.8125))
    assert high.level == 2
    assert "R_MODEL_HIGH" in high.rules_fired
    assert "R_MODEL_COST" in high.rules_fired


def test_authority_purpose_is_level_2():
    out = evaluate(_facts(transfer_purpose="authority_request", call_state="none"))
    assert out.level == 2
    assert "R_PURPOSE_HIGH_RISK" in out.rules_fired
    assert out.intervention == "three_button"


def test_capability_is_banner_plus_extra_auth_not_a_workflow():
    out = evaluate(_facts(device_state="capability_detected", call_state="none", p_fraud=0.0))
    assert out.level == 1
    assert out.extra_auth is True
    assert out.intervention == "banner"
    assert out.intervention != "security_workflow"


def test_low_baseline_behaviour_is_not_used_for_level_2():
    out = evaluate(
        _facts(
            call_state="active",
            new_payee=True,
            behaviour_p_rarity=0.01,
            same_task_sessions=1,
            funds_events=[],
            task_type="new_typed",
        )
    )
    assert out.level < 2
    assert "R_BEHAVIOUR_GUIDED" not in out.rules_fired
    rare = next(item for item in out.signals if item.signal_id == "behaviour_rare")
    assert rare.status == "unknown"
    assert out.baseline_quality == "low"


def test_user_floor_only_raises():
    raised = evaluate(_facts(user_level_floor=2, call_state="none", new_payee=False))
    assert raised.level >= 2
    kept = evaluate(_ba_man(_at(12), payee="9" * 64))
    assert kept.level == 2


def test_policy_score_p95_under_100ms():
    samples = []
    for index in range(80):
        out = evaluate(_ba_man(_at(9), payee=f"{index:064x}"))
        samples.append(out.score_ms)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    p99 = samples[max(0, int(len(samples) * 0.99) - 1)]
    print(f"policy p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms n={len(samples)}")
    assert p95 < 100
