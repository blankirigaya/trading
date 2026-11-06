"""The board must expose real structure when history is available.

Without bars the model sees one day-candle and can only guess at levels; it must
then be told plainly that it is looking at a session range, never an ATR.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.domain.market import Candle, Instrument, MarketState

NOW = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)


def _state() -> MarketState:
    candle = Candle(
        start_timestamp=NOW,
        end_timestamp=NOW,
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=1000,
    )
    return MarketState(
        snapshot_id="s",
        instrument=Instrument(instrument_key="1", symbol="INFY", exchange="NSE"),
        timestamp=NOW,
        last_price=Decimal("103"),
        previous_close=Decimal("100"),
        recent_ticks=[],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


def _bars(count: int = 6) -> tuple[Candle, ...]:
    candles = []
    for index in range(count):
        price = Decimal("100") + index
        candles.append(
            Candle(
                start_timestamp=NOW - timedelta(minutes=5 * (count - index)),
                end_timestamp=NOW - timedelta(minutes=5 * (count - index - 1)),
                open=price,
                high=price + Decimal("1.5"),
                low=price - Decimal("0.5"),
                close=price,
                volume=1000,
            )
        )
    return tuple(candles)


def _line(block: str, symbol: str = "INFY") -> str:
    return next(line for line in block.splitlines() if line.strip().startswith(f"{symbol}:"))


def test_board_renders_bars_and_real_atr() -> None:
    block = PortfolioManagerAgent._board_block([_state()], bars={"INFY": _bars()})
    line = _line(block)

    assert "5m=" in line
    assert "ATR5=" in line
    # High/low/close triplets, oldest first.
    assert "101.50/99.50/100" in line
    assert "range=" not in line


def test_board_falls_back_to_range_without_history() -> None:
    block = PortfolioManagerAgent._board_block([_state()])
    line = _line(block)

    assert "range=5.00 (5.00%)" in line
    assert "ATR5" not in line
    assert "5m=" not in line


def test_board_explains_the_units() -> None:
    block = PortfolioManagerAgent._board_block([_state()], bars={"INFY": _bars()})
    assert "H/L/C, oldest first" in block
    assert "average true range" in block
