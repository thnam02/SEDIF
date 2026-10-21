"""Cùng một mốc thời gian, dữ liệu đầy đủ và dữ liệu đã cắt phải ra cùng đặc trưng."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features import compute_features
from features.store import FeatureStore, using_store


def test_cut_at_as_of_matches_full_store() -> None:
    store = FeatureStore(SIM / "out" / "observed")
    rng = np.random.default_rng(42)
    codes = rng.choice(len(store.session_id), size=200, replace=False)
    for code in codes:
        session_id = str(store.session_id[int(code)])
        when = pd.Timestamp(int(store.sess_start[int(code)]) + 3_600 * 1_000_000_000, tz="UTC")
        cut = store.before(when)
        for view in ("single_bank", "network"):
            with using_store(store):
                full = compute_features(session_id, when, view)
            with using_store(cut):
                sliced = compute_features(session_id, when, view)
            assert full == sliced, (session_id, view, _diff(full, sliced))


def _diff(left: dict, right: dict) -> dict:
    return {key: (left[key], right[key]) for key in left if left[key] != right[key]}
