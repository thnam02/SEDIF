"""Bootstrap cụm theo vụ. Phiên không có vụ đứng riêng một cụm."""

from __future__ import annotations

import numpy as np
import pandas as pd


def cluster_of(case_id: pd.Series, session_id: pd.Series) -> np.ndarray:
    case = case_id.astype("string")
    blank = case.isna() | case.isin(["", "<NA>", "nan", "None"])
    session = session_id.astype(str)
    return np.where(blank.to_numpy(), session.to_numpy(), case.astype(str).to_numpy())


def cluster_index(clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Phiên sắp theo cụm: (thứ tự, vị trí đầu, số phiên)."""
    order = np.argsort(clusters, kind="mergesort")
    sorted_ids = clusters[order]
    if len(sorted_ids) == 0:
        empty = np.array([], dtype=np.int64)
        return order, empty, empty
    change = np.flatnonzero(sorted_ids[1:] != sorted_ids[:-1]) + 1
    starts = np.r_[0, change].astype(np.int64)
    counts = np.diff(np.r_[starts, len(clusters)]).astype(np.int64)
    return order, starts, counts


def bootstrap_values(groups: tuple[np.ndarray, np.ndarray, np.ndarray], statistic, draws: int, seed: int) -> np.ndarray:
    """Mỗi lần rút cả cụm, rồi tính statistic trên chỉ số phiên đã rút."""
    order, starts, counts = groups
    n = len(counts)
    rng = np.random.default_rng(seed)
    out = np.empty(draws, dtype=float)
    for draw in range(draws):
        picked = rng.integers(0, n, size=n)
        span = counts[picked]
        total = int(span.sum())
        if total == 0:
            out[draw] = np.nan
            continue
        repeated = np.repeat(picked, span)
        block = np.repeat(np.cumsum(span) - span, span)
        within = np.arange(total) - block
        index = order[starts[repeated] + within]
        out[draw] = float(statistic(index))
    return out


def interval(samples: np.ndarray) -> tuple[float, float]:
    low, high = np.nanpercentile(samples, [2.5, 97.5])
    return float(low), float(high)


def format_ci(point: float, samples: np.ndarray, percent: bool = False) -> str:
    low, high = interval(samples)
    if percent:
        return f"{100 * point:.1f}% [{100 * low:.1f}–{100 * high:.1f}]"
    return f"{point:.3f} [{low:.3f}–{high:.3f}]"
