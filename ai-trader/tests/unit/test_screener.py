"""Deterministic intraday screener: gap, volume, location, expansion ranking."""

from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.screening.screener import rank_candidates

NOW = datetime(2026, 10, 26, 6, 30, tzinfo=UTC)


def _instrument(symbol: str) -> Instrument:
    return Instrument(instrument_key="k-" + symbol, symbol=symbol, exchange="NSE")


def _tick(symbol: str, price: str) -> MarketTick:
    return MarketTick(
        symbol=symbol,
        last_price=Decimal(price),
        exchange_timestamp=NOW,
        received_timestamp=NOW,
        processed_timestamp=NOW,
    )


def _candle(open_: str, high: str, low: str, close: str, volume: int | None = 1000) -> Candle:
    return Candle(
        start_timestamp=NOW,
        end_timestamp=NOW,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def _state(
    symbol: str,
    last: str,
    prev_close: str | None = "100",
    volume_now: int | None = 1000,
    history_volumes: tuple[int, ...] = (1000, 1000, 1000),
) -> MarketState:
    history = [_candle("100", "101", "99", "100", volume=v) for v in history_volumes]
    current = _candle("100", "102", "99", last, volume=volume_now)
    return MarketState(
        snapshot_id="s-" + symbol,
        instrument=_instrument(symbol),
        timestamp=NOW,
        last_price=Decimal(last),
        previous_close=Decimal(prev_close) if prev_close is not None else None,
        recent_ticks=[_tick(symbol, last)],
        current_1m_candle=current,
        recent_1m_candles=history,
    )


def test_gap_up_with_volume_surge_ranks_first() -> None:
    hot = _state("HOT", "105", prev_close="100", volume_now=5000)
    flat = _state("FLAT", "100.1", prev_close="100", volume_now=1000)
    ranked = rank_candidates([flat, hot])
    assert [c.symbol for c in ranked] == ["HOT", "FLAT"]
    assert ranked[0].score > ranked[1].score
    assert any("gap" in reason for reason in ranked[0].reasons)
    assert any("vol" in reason for reason in ranked[0].reasons)


def test_near_flat_symbol_scores_tiny_and_ranks_last() -> None:
    flat = _state("FLAT", "100.1", prev_close="100", volume_now=1000)
    hot = _state("HOT", "105", prev_close="100", volume_now=5000)
    ranked = rank_candidates([flat, hot])
    assert ranked[-1].symbol == "FLAT"
    assert ranked[-1].score < 0.15
    assert ranked[0].score > 0.5


def test_missing_inputs_do_not_crash_and_carry_no_weight() -> None:
    thin = _state("THIN", "102", prev_close=None, volume_now=None, history_volumes=())
    ranked = rank_candidates([thin])
    assert len(ranked) == 1
    # Location component alone still scores: pinned at the candle high.
    assert ranked[0].score > 0
    assert ranked[0].reasons


def test_empty_board_and_nonpositive_prices() -> None:
    assert rank_candidates([]) == []
    bad = _state("BAD", "100", prev_close="100")
    bad = bad.model_copy(update={"last_price": Decimal("0")})
    assert rank_candidates([bad]) == []


def test_top_n_bounds_the_shortlist() -> None:
    board = [_state(f"S{i}", str(100 + i), prev_close="100") for i in range(8)]
    assert len(rank_candidates(board)) == 5
    assert len(rank_candidates(board, top_n=3)) == 3
    assert rank_candidates(board, top_n=0) == []
