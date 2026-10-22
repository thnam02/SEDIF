"""Đặc trưng dùng chung lúc huấn luyện và lúc phục vụ.

Thư viện này chỉ nhận bảng đã quan sát. Không mở dữ liệu nhãn.
"""

from features.service import FeatureService, action_for, actions_from_families, decide_levels, fit_tau, session_families, severity
from features.store import FEATURE_COLUMNS, LONG_TERM, NETWORK_ONLY, PROFILES, FeatureStore, using_store


def compute_features(session_id: str, as_of_ts, view: str = "single_bank", profile: str = "advanced") -> dict:
    """Đặc trưng của một phiên tại thời điểm as_of_ts. Chỉ dùng sự kiện có ts < as_of_ts."""
    if view not in {"single_bank", "network"}:
        raise ValueError("view phải là single_bank hoặc network")
    if profile not in PROFILES:
        raise ValueError("profile phải là basic, standard hoặc advanced")
    return using_store.current().compute(str(session_id), as_of_ts, view, profile)


__all__ = [
    "FEATURE_COLUMNS",
    "LONG_TERM",
    "NETWORK_ONLY",
    "PROFILES",
    "FeatureService",
    "FeatureStore",
    "action_for",
    "actions_from_families",
    "compute_features",
    "decide_levels",
    "fit_tau",
    "session_families",
    "severity",
    "using_store",
]
