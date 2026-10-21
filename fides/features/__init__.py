"""Đặc trưng dùng chung lúc huấn luyện và lúc phục vụ.

Thư viện này chỉ nhận bảng đã quan sát. Không mở dữ liệu nhãn.
"""

from features.store import FEATURE_COLUMNS, NETWORK_ONLY, FeatureStore, using_store


def compute_features(session_id: str, as_of_ts, view: str = "single_bank") -> dict:
    """Đặc trưng của một phiên tại thời điểm as_of_ts. Chỉ dùng sự kiện có ts < as_of_ts."""
    if view not in {"single_bank", "network"}:
        raise ValueError("view phải là single_bank hoặc network")
    return using_store.current().compute(str(session_id), as_of_ts, view)


__all__ = ["FEATURE_COLUMNS", "NETWORK_ONLY", "FeatureStore", "compute_features", "using_store"]
