"""Volatility measures used to scale trading plans to each symbol.

A fixed-percentage target means something different on every symbol: 1% is noise
on a stock that ranges 4% a day and a huge move on one that ranges 0.4%. ATR is
the symbol's own recent travel, so plans expressed in ATR mean the same thing
across the board.
"""

from __future__ import annotations

from decimal import Decimal

from ai_trader.domain.market import Candle

DEFAULT_ATR_PERIOD = 14


def true_ranges(candles: list[Candle]) -> list[Decimal]:
    """Per-candle true range: the largest of the bar range and its two gaps."""
    ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return ranges


def atr(candles: list[Candle], period: int = DEFAULT_ATR_PERIOD) -> Decimal | None:
    """Average true range over the last ``period`` bars.

    Returns ``None`` when there is not enough data to measure anything, so
    callers can fall back to fixed rules instead of inventing a volatility
    number. Fewer than ``period`` bars are averaged as-is rather than dropped.
    """
    if len(candles) < 2 or period < 1:
        return None
    ranges = true_ranges(candles)[-period:]
    if not ranges:
        return None
    return sum(ranges) / Decimal(len(ranges))


def atr_percent(candles: list[Candle], period: int = DEFAULT_ATR_PERIOD) -> Decimal | None:
    """ATR as a percentage of the last close, for prompt rendering."""
    value = atr(candles, period)
    if value is None or not candles:
        return None
    close = candles[-1].close
    if close <= 0:
        return None
    return value / close * Decimal(100)
