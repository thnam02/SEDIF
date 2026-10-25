"""Hợp đồng /respond: chỉ nâng level, countdown, hold. Không cần Redis."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")

from engine.decide import apply_contract, parse_ts

ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def _state(level, decided, guardian=False, funds=None):
    funds_status, _hold = ("held", None) if level in {"medium", "high"} else ("released", None)
    return {
        "level": level,
        "funds_status": funds or funds_status,
        "countdown_until": None,
        "hold_until": None,
        "decided_at": parse_ts(decided),
        "has_guardian": guardian,
    }


def test_hung_up_starts_countdown_and_keeps_funds():
    state = _state("high", "2026-09-21T10:15:00+07:00")
    out = apply_contract(state, {"hung_up": True}, parse_ts("2026-09-21T10:20:00+07:00"))
    assert out["level"] == "high"
    assert out["funds_status"] == "held"
    assert out["countdown_until"] == datetime(2026, 9, 21, 10, 30, tzinfo=ICT)
    assert out["hold_until"] == datetime(2026, 9, 22, 10, 15, tzinfo=ICT)


def test_relative_without_guardian_keeps_banner():
    state = _state("banner", "2026-09-22T19:40:00+07:00")
    out = apply_contract(state, {"who_guides": "relative"}, parse_ts("2026-09-22T19:41:00+07:00"))
    assert out["level"] == "banner"
    assert out["funds_status"] == "released"
    assert out["gate"] == "short_wait"
    assert out["hold_until"] is None


def test_relative_with_guardian_keeps_level():
    state = _state("medium", "2026-09-22T19:40:00+07:00", guardian=True)
    out = apply_contract(state, {"who_guides": "relative"}, parse_ts("2026-09-22T19:41:00+07:00"))
    assert out["level"] == "medium"
    assert out["gate"] == "guardian_confirm_requested"
    assert out["funds_status"] == "held"


def test_authority_raises_to_high_and_friend_does_not_lower():
    state = _state("banner", "2026-09-21T10:15:00+07:00")
    raised = apply_contract(state, {"who_guides": "authority"}, parse_ts("2026-09-21T10:16:00+07:00"))
    assert raised["level"] == "high"
    assert raised["funds_status"] == "held"
    assert raised["hold_until"] == datetime(2026, 9, 22, 10, 15, tzinfo=ICT)
    kept = apply_contract(
        {**state, "level": "high", "funds_status": "held"},
        {"who_guides": "friend"},
        parse_ts("2026-09-21T10:16:00+07:00"),
    )
    assert kept["level"] == "high"


def test_proceed_before_countdown_does_not_release():
    state = _state("high", "2026-09-21T10:15:00+07:00")
    hung = apply_contract(state, {"hung_up": True}, parse_ts("2026-09-21T10:20:00+07:00"))
    early = apply_contract(
        {**state, "countdown_until": hung["countdown_until"], "funds_status": "held"},
        {"proceed": True},
        parse_ts("2026-09-21T10:25:00+07:00"),
    )
    assert early["funds_status"] == "held"
    late = apply_contract(
        {**state, "countdown_until": hung["countdown_until"], "funds_status": "held"},
        {"proceed": True},
        parse_ts("2026-09-21T10:30:00+07:00"),
    )
    assert late["funds_status"] == "released"


def test_cancel_and_guardian_confirm():
    state = _state("high", "2026-09-21T10:15:00+07:00")
    cancelled = apply_contract(state, {"cancel": True}, parse_ts("2026-09-21T10:16:00+07:00"))
    assert cancelled["funds_status"] == "cancelled"
    released = apply_contract(state, {"guardian_confirm": True}, parse_ts("2026-09-21T10:16:00+07:00"))
    assert released["funds_status"] == "released"
    assert released["level"] == "high"
