"""Cách nhập người nhận phải có mặt trước khi chấm điểm."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO)]

from engine.app import apply_add_method, with_add_method


def test_known_methods_are_written_onto_the_row():
    for method in ("typed", "paste", "qr", "contact"):
        row = {"add_method": None}
        assert apply_add_method({"add_method": method, "decision_id": "d"}, row) is True
        assert row["add_method"] == method


def test_missing_add_method_warns_and_is_visible(caplog):
    row = {}
    with caplog.at_level(logging.WARNING, logger="engine"):
        known = apply_add_method({"decision_id": "dec-1"}, row)
    assert known is False
    assert "add_method" not in row
    assert "add_method_missing" in caplog.text
    assert with_add_method(["new_payee"], False) == ["new_payee", "add_method_missing"]
    assert with_add_method(["new_payee"], True) == ["new_payee"]
