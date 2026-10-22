"""Nạp artifact model-v2.1 và chấm lại test. Khớp bảng đã ghi, sai số 0."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.model_v21 import ARTIFACTS, PACKAGED, prepare, score_saved


def _same(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and expected.keys() == actual.keys() and all(_same(actual[key], expected[key]) for key in expected)
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and float(actual) == float(expected)
    return actual == expected


def test_packaged_profiles_match_table():
    table = json.loads((ARTIFACTS / "results.json").read_text(encoding="utf-8"))
    frames, bundle = prepare()
    for name in PACKAGED:
        actual = score_saved(name, frames, bundle)
        expected = next(row for row in table["rows"] if row["profile"] == name)
        for key in ("tau_hi", "saved", "saved_low", "saved_high", "damage", "rate", "recall_at_1", "legit_mh", "medium_loan", "guided_hangup", "scenario_recall"):
            assert _same(actual[key], expected[key]), (name, key, actual[key], expected[key])
