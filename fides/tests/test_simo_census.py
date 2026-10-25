"""Đối chiếu listing SIMO với truth/. Engine không import module này."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OBSERVED = ROOT / "fides-sim" / "out" / "observed"
TRUTH = ROOT / "fides-sim" / "out" / "truth"
START = pd.Timestamp("2026-07-01", tz="Asia/Ho_Chi_Minh")
END = pd.Timestamp("2026-09-29", tz="Asia/Ho_Chi_Minh")


def _share(frame: pd.DataFrame, mules: set[str]) -> float:
    if len(frame) == 0:
        return 0.0
    return float(frame["account_id"].astype(str).isin(mules).mean())


def test_simo_listing_windows_and_mule_share() -> None:
    listings = pd.read_parquet(OBSERVED / "simo_list.parquet", columns=["account_id", "listed_at", "source"])
    truth = pd.read_parquet(TRUTH / "accounts_truth.parquet", columns=["account_id", "is_mule"])
    mules = set(truth.loc[truth["is_mule"].fillna(False).astype(bool), "account_id"].astype(str))
    ready = listings.loc[listings["listed_at"] < START]
    inside = listings.loc[(listings["listed_at"] >= START) & (listings["listed_at"] < END)]
    loaded = listings.loc[listings["listed_at"] < END]
    print(f"simo trước sim_start {len(ready)} mule { _share(ready, mules):.2%}")
    print(f"simo trong kỳ {len(inside)} mule {_share(inside, mules):.2%}")
    print(f"simo đã nạp {len(loaded)} mule {_share(loaded, mules):.2%}")
    assert len(ready) > 0 and len(inside) > 0
    assert _share(loaded, mules) < 1
