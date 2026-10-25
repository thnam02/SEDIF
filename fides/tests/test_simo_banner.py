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
    row = {"simo_listed": 1, "segment": "thường", "payee_transfers_before_listing": 2}
    assert assign_level(0.0, 10_000, families, "thường", row) == "banner"
    reasons = reasons_for(row, families)
    assert reasons == [{
        "code": "simo_listed",
        "text": "Tài khoản nhận đã bị báo cáo có dấu hiệu liên quan đến lừa đảo. Cơ quan công an và ngân hàng không bao giờ yêu cầu chuyển tiền vào tài khoản 'tạm giữ' hay 'xác minh'. Nếu ai đó đang hướng dẫn bạn chuyển khoản, hãy dừng lại.",
    }]


def test_repeat_payee_before_listing_is_info():
    families = {"call": 0, "entry": 0, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "payee_transfers_before_listing": 3, "payee_first_days_before_listing": 60}
    assert assign_level(0.0, 10_000, families, "thường", row) == "info"
    assert reasons_for(row, families) == [{
        "code": "simo_info",
        "text": "Bạn đã chuyển cho người nhận này từ trước khi tài khoản được đưa vào danh sách cần chú ý.",
    }]


def test_recent_repeat_stays_banner():
    families = {"call": 0, "entry": 0, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "payee_transfers_before_listing": 3, "payee_first_days_before_listing": 59}
    assert assign_level(0.0, 10_000, families, "thường", row) == "banner"
    assert reasons_for(row, families)[0]["code"] == "simo_listed"


def test_listed_payee_with_family_keeps_level():
    families = {"call": 1, "entry": 0, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "in_call": 1, "payee_transfers_before_listing": 5}
    assert assign_level(0.0, 10_000, families, "thường", row) == "none"
    assert assign_level(0.9, 50_000_000, families, "thường", row) == "medium"


def test_listed_payee_does_not_lower_medium():
    families = {"call": 1, "entry": 1, "payee": 0, "mobilization": 0}
    row = {"simo_listed": 1, "in_call": 1}
    assert assign_level(0.9, 50_000_000, families, "thường", row) == "medium"
    codes = [item["code"] for item in reasons_for(row, families)]
    assert codes[-1] == "simo_listed"
    assert len(codes) <= 3
