"""Lời nhắc cuộc gọi: tối đa 3 lần, demo thì không đếm."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "bank-core")]

from notice import notice_allowed, notice_limits


def test_limits_come_from_config():
    assert notice_limits() == (3, 30)


def test_demo_skips_the_cap():
    assert notice_allowed(3, 3, True)
    assert not notice_allowed(3, 3, False)
    assert notice_allowed(2, 3, False)
