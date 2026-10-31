"""SQLite retry helper: transient lock errors are retried, everything else is not.

A lock held past the busy timeout used to surface as a lost trade or a dead
review loop. These tests pin the retry contract.
"""

import pytest
from sqlalchemy.exc import OperationalError

from ai_trader.persistence import retry


def _locked(message: str = "database is locked") -> OperationalError:
    return OperationalError("INSERT", {}, Exception(message))


async def test_retries_transient_operational_error_then_succeeds() -> None:
    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _locked()
        return "ok"

    assert await retry.with_sqlite_retry("test", flaky) == "ok"
    assert calls == 3


async def test_gives_up_after_the_configured_attempts(monkeypatch) -> None:
    monkeypatch.setattr(retry, "RETRY_DELAYS_SECONDS", (0.0, 0.0))
    calls = 0

    def always_locked() -> None:
        nonlocal calls
        calls += 1
        raise _locked()

    with pytest.raises(OperationalError):
        await retry.with_sqlite_retry("test", always_locked)
    assert calls == 3  # first attempt plus the delay table's two extra tries


async def test_non_sqlite_errors_are_not_retried() -> None:
    calls = 0

    def broken() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("bad data")

    with pytest.raises(ValueError):
        await retry.with_sqlite_retry("test", broken)
    assert calls == 1
