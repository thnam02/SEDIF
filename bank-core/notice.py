"""Giới hạn lời nhắc cuộc gọi. Số lần và cửa sổ là GIẢ ĐỊNH trong fides-sim/config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def notice_limits() -> tuple[int, int]:
    data = yaml.safe_load((ROOT / "fides-sim" / "config.yaml").read_text(encoding="utf-8"))
    decision = data.get("decision") or {}
    return int(decision.get("call_notice_max", 3)), int(decision.get("call_notice_days", 30))


def notice_allowed(shown: int, limit: int, demo: bool) -> bool:
    if demo:
        return True
    return shown < limit
