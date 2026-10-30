"""Đồng hồ demo. Ngày cố định 20/09/2026, giờ là giờ địa phương, trôi theo thời gian thật."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
DEMO_DAY = datetime(2026, 9, 20, tzinfo=ICT)


def _wall_on_demo_day() -> datetime:
    wall = datetime.now(ICT)
    return DEMO_DAY.replace(hour=wall.hour, minute=wall.minute, second=wall.second, microsecond=wall.microsecond)


class DemoClock:
    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self.paused = False
        self.feed = False
        self._sim = _wall_on_demo_day()
        self._real = time.monotonic()

    def now(self) -> datetime:
        if self.paused:
            return self._sim
        elapsed = time.monotonic() - self._real
        return self._sim + timedelta(seconds=elapsed * self.speed)

    def reanchor(self) -> datetime:
        """Gắn lại giờ địa phương hiện tại lên 20/09/2026 và để đồng hồ chạy thật."""
        self._sim = _wall_on_demo_day()
        self._real = time.monotonic()
        self.paused = False
        self.speed = 1.0
        self.feed = False
        return self._sim

    def pause(self) -> datetime:
        self._sim = self.now()
        self.paused = True
        return self._sim

    def resume(self) -> datetime:
        self.feed = True
        if self.paused:
            self._real = time.monotonic()
            self.paused = False
        return self.now()

    def seek(self, stamp: datetime) -> datetime:
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ICT)
        self._sim = stamp.astimezone(ICT)
        self._real = time.monotonic()
        self.paused = True
        return self._sim

    def advance(self, seconds: float) -> datetime:
        self._sim = self.now() + timedelta(seconds=seconds)
        self._real = time.monotonic()
        return self._sim

    def state(self) -> dict:
        return {"ts": self.now().isoformat(timespec="microseconds"), "paused": self.paused, "speed": self.speed}
