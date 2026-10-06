from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("FakeClock requires an aware datetime")
        self.current = current.astimezone(UTC)

    def now(self) -> datetime:
        return self.current

    def advance_seconds(self, seconds: int) -> None:
        from datetime import timedelta

        self.current += timedelta(seconds=seconds)
