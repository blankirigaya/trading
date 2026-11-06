"""The manager must know the real IST time, not infer it from UTC payloads.

A model reading "06:07Z" as local time concludes the market just opened even
when it is 11:37 IST, which changes every judgement it makes about how much of
the day is left. These tests pin the clock text and its inclusion in context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_trader.ai.portfolio_manager import PortfolioManagerAgent, market_clock


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 6, hour, minute, tzinfo=UTC)


def test_midday_utc_is_reported_as_afternoon_ist() -> None:
    # 06:07 UTC is 11:37 IST: nearly half the session is gone.
    text = market_clock(_utc(6, 7))
    assert "11:37 IST" in text
    assert "open for 142 minutes" in text
    assert "UTC, not IST" in text
    assert "fresh open" in text


def test_pre_open_reports_minutes_until_the_bell() -> None:
    # 03:00 UTC is 08:30 IST, 45 minutes before the open.
    text = market_clock(_utc(3, 0))
    assert "08:30 IST" in text
    assert "opens in 45 minutes" in text


def test_after_square_off_forbids_new_entries() -> None:
    # 10:30 UTC is 16:00 IST, past the 15:10 square-off.
    text = market_clock(_utc(10, 30))
    assert "past the 15:10 IST square-off" in text
    assert "no new entries" in text


class _CapturingAI:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_prompt = ""

    @property
    def model_name(self) -> str:
        return "capturing"

    async def structured_completion(
        self, system_prompt: Any, user_prompt: Any, response_model: Any, **kwargs: Any
    ) -> Any:
        raise AssertionError("not used in this test")


def test_context_puts_the_clock_before_the_payload() -> None:
    manager = PortfolioManagerAgent(_CapturingAI(), "v16")  # type: ignore[arg-type]
    from decimal import Decimal

    from ai_trader.domain.market import MarketTick
    from ai_trader.domain.portfolio import PortfolioState

    tick = MarketTick(
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("100"),
        exchange_timestamp=_utc(6, 7),
        received_timestamp=_utc(6, 7),
        processed_timestamp=_utc(6, 7),
        raw_payload={},
    )
    context = manager._context(tick, PortfolioState(cash=Decimal("100000")), None)
    assert context.startswith("Current time: 11:37 IST")
    assert "timestamps UTC" in context
