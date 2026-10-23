"""Đặc trưng online. Mỗi cột đọc Redis tại thời điểm hỏi, không đọc lại Postgres."""

from engine.features.online import COLUMNS, FAMILY_COLUMNS, OnlineStore

__all__ = ["COLUMNS", "FAMILY_COLUMNS", "OnlineStore"]
