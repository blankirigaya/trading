"""Board filtering: only fresh, complete states reach the AI's pick."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_trader.application.fyers_session import tradeable_states
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick


def _state(symbol: str, *, age_seconds: float = 1.0, with_candle: bool = True) -> MarketState:
    now = datetime.now(UTC)
    tick_time = now - timedelta(seconds=age_seconds)
    tick = MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("100"),
        exchange_timestamp=tick_time,
        received_timestamp=tick_time,
        processed_timestamp=tick_time,
        raw_payload={},
    )
    candle = (
        Candle(
            start_timestamp=tick_time,
            end_timestamp=tick_time,
            open=Decimal("99"),
            high=Decimal("101"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=1000,
        )
        if with_candle
        else None
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=tick_time,
        last_price=Decimal("100"),
        recent_ticks=[tick],
        current_1m_candle=candle,
    )


def test_board_keeps_fresh_states_sorted_by_symbol() -> None:
    now = datetime.now(UTC)
    board = tradeable_states(
        [_state("TCS"), _state("INFY"), _state("RELIANCE")], now, max_age_seconds=120
    )
    assert [s.instrument.symbol for s in board] == ["INFY", "RELIANCE", "TCS"]


def test_board_drops_stale_and_partial_states() -> None:
    now = datetime.now(UTC)
    board = tradeable_states(
        [_state("STALE", age_seconds=600), _state("NOCANDLE", with_candle=False)],
        now,
        max_age_seconds=120,
    )
    assert board == []


def test_empty_feed_gives_empty_board() -> None:
    assert tradeable_states([], datetime.now(UTC), 120) == []
