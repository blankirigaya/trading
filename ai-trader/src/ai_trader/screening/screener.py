"""Deterministic intraday screener: rank a board so the AI reviews signals first.

The screener never trades and never overrules the AI. Each review it scores
every board symbol on gap, volume surge, close location, and range expansion,
all normalized to 0..1 and combined with fixed weights. Missing inputs simply
drop that component (weights renormalize), so thin-history symbols still rank.
The top names are marked in the prompt board block; the AI focuses there but
may overrule with written justification.
"""

from dataclasses import dataclass

from ai_trader.domain.market import MarketState

_SHORTLIST_SIZE = 5

_WEIGHT_GAP = 0.35
_WEIGHT_VOLUME = 0.30
_WEIGHT_LOCATION = 0.20
_WEIGHT_EXPANSION = 0.15


@dataclass(frozen=True)
class ScreenedCandidate:
    """One ranked symbol with human-readable, number-backed reasons."""

    symbol: str
    score: float
    reasons: tuple[str, ...] = ()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _gap_component(state: MarketState) -> tuple[float, str | None]:
    prev = state.previous_close
    if prev is None or prev <= 0:
        return 0.0, None
    gap_pct = float((state.last_price - prev) / prev) * 100.0
    return _clamp01(abs(gap_pct) / 5.0), f"gap {gap_pct:+.2f}%"


def _volume_component(state: MarketState) -> tuple[float, str | None]:
    current = state.current_1m_candle
    history = [c for c in state.recent_1m_candles if c.volume]
    if current is None or current.volume is None or len(history) < 3:
        return 0.0, None
    average = sum(c.volume or 0 for c in history) / len(history)
    if average <= 0:
        return 0.0, None
    surge = current.volume / average
    return _clamp01((surge - 1.0) / 4.0), f"vol {surge:.1f}x avg"


def _location_component(state: MarketState) -> tuple[float, str | None]:
    candle = state.current_1m_candle
    if candle is None or candle.high <= candle.low:
        return 0.0, None
    location = float((state.last_price - candle.low) / (candle.high - candle.low))
    location = _clamp01(location)
    # Distance from mid-range: pinning the high or the low is the signal,
    # drifting mid-range is noise.
    return abs(location - 0.5) * 2.0, f"range-pos {location:.2f}"


def _expansion_component(state: MarketState) -> tuple[float, str | None]:
    current = state.current_1m_candle
    history = state.recent_1m_candles
    if current is None or len(history) < 3 or state.last_price <= 0:
        return 0.0, None
    current_range = float((current.high - current.low) / state.last_price)
    past_ranges = sorted(
        float((c.high - c.low) / c.close)
        for c in history
        if c.close and c.close > 0 and c.high >= c.low
    )
    if not past_ranges:
        return 0.0, None
    median = past_ranges[len(past_ranges) // 2]
    if median <= 0:
        return 0.0, None
    expansion = current_range / median
    return _clamp01((expansion - 1.0) / 2.0), f"range {expansion:.1f}x median"


def rank_candidates(
    board: list[MarketState], *, top_n: int = _SHORTLIST_SIZE
) -> list[ScreenedCandidate]:
    """Score every board symbol; best first. Empty board yields empty list."""
    scored: list[ScreenedCandidate] = []
    for state in board:
        if state.last_price <= 0:
            continue
        parts = [
            (_WEIGHT_GAP, _gap_component(state)),
            (_WEIGHT_VOLUME, _volume_component(state)),
            (_WEIGHT_LOCATION, _location_component(state)),
            (_WEIGHT_EXPANSION, _expansion_component(state)),
        ]
        # Components without data contribute nothing and carry no weight.
        usable = [(weight, score, reason) for weight, (score, reason) in parts if reason]
        if not usable:
            scored.append(ScreenedCandidate(symbol=state.instrument.symbol, score=0.0))
            continue
        total_weight = sum(weight for weight, _, _ in usable)
        score = sum(weight * s for weight, s, _ in usable) / total_weight
        reasons = tuple(reason for _, _, reason in usable)
        scored.append(
            ScreenedCandidate(symbol=state.instrument.symbol, score=score, reasons=reasons)
        )
    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    return scored[: max(0, top_n)]
