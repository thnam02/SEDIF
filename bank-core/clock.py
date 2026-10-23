"""Đồng hồ demo. Bắt đầu 2026-09-20, nhanh ×60, có pause và resume."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
START = datetime(2026, 9, 20, tzinfo=ICT)


class DemoClock:
    def __init__(self, speed: float = 60.0):
        self.speed = speed
        self.paused = True
        self._sim = START
        self._real = time.monotonic()

    def now(self) -> datetime:
        if self.paused:
            return self._sim
        elapsed = time.monotonic() - self._real
        return self._sim + timedelta(seconds=elapsed * self.speed)

    def pause(self) -> datetime:
        self._sim = self.now()
        self.paused = True
        return self._sim

    def resume(self) -> datetime:
        if self.paused:
            self._real = time.monotonic()
            self.paused = False
        return self.now()

    def state(self) -> dict:
        return {"ts": self.now().isoformat(timespec="microseconds"), "paused": self.paused, "speed": self.speed}
