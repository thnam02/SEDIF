"""Ghi bảng giả lập ra Parquet."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ZONES = {"observed", "truth"}


def write(df: pd.DataFrame, zone: str, name: str) -> Path:
    """Ghi `df` vào `out/<zone>/<name>.parquet`.

    `zone` chỉ được là `observed` hoặc `truth`.
    """
    if zone not in ZONES:
        raise ValueError(f"zone phải thuộc {sorted(ZONES)}, nhận được {zone!r}")
    out_dir = ROOT / "out" / zone
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return path
