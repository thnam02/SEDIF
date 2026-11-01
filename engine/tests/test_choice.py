"""Lựa chọn ghi vào sổ phản hồi. abandoned khác cancel."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO)]

from engine.audit import reject_choice


def test_abandoned_is_a_choice_and_cancel_stays_separate():
    assert reject_choice("abandoned", 0) is False
    assert reject_choice("cancel", 0) is False
    assert reject_choice("pause", 10) is False
    assert reject_choice("continue", 10) is False
    assert reject_choice("expire", 0) is True
    assert reject_choice("abandoned", -1) is True
    assert reject_choice("abandoned", True) is True
