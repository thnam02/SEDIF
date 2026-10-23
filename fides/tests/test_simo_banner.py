"""Người nhận trong danh sách SIMO được ít nhất banner, kèm đúng câu chữ."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(REPO)]

from engine.decide import assign_level, reasons_for


def test_listed_payee_is_at_least_banner():
    families = {"call": 0, "entry": 0, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "segment": "thường"}
    assert assign_level(0.0, 10_000, families, "thường", row) == "banner"
    reasons = reasons_for(row, families)
    assert reasons == [{"code": "simo_listed", "text": "Người nhận có trong danh sách cần chú ý."}]


def test_listed_payee_does_not_lower_medium():
    families = {"call": 1, "entry": 1, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "in_call": 1}
    assert assign_level(0.9, 50_000_000, families, "thường", row) == "medium"
    codes = [item["code"] for item in reasons_for(row, families)]
    assert codes[-1] == "simo_listed"
    assert len(codes) <= 3
