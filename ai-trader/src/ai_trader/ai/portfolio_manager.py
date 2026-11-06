from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_trader.ai.base import AIProvider
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioDecision, ProviderMetadata
from ai_trader.domain.market import Candle, MarketState, MarketTick
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.news.review_context import NewsReviewBundle

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN_IST = time(9, 15)
_SQUARE_OFF_IST = time(15, 10)


def market_clock(now: datetime) -> str:
    """Describe the current time unambiguously, in IST, for the manager prompt.

    Payload timestamps are UTC ISO strings. A model left to interpret them
    itself reads "06:07Z" as 06:07 local and concludes the market just opened,
    even at 11:37 IST — which changes what it thinks it is looking at. This
    states the IST wall clock and how far through the session we actually are,
    so "early session" conclusions are grounded in fact.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local = now.astimezone(IST)
    open_at = local.replace(
        hour=_MARKET_OPEN_IST.hour,
        minute=_MARKET_OPEN_IST.minute,
        second=0,
        microsecond=0,
    )
    flat_at = local.replace(
        hour=_SQUARE_OFF_IST.hour,
        minute=_SQUARE_OFF_IST.minute,
        second=0,
        microsecond=0,
    )
    clock = f"Current time: {local:%H:%M} IST, {local:%A %d %B %Y}."
    minutes_since_open = int((local - open_at).total_seconds() // 60)
    minutes_to_flat = int((flat_at - local).total_seconds() // 60)
    if minutes_since_open < 0:
        phase = f"The NSE cash session opens in {abs(minutes_since_open)} minutes (09:15 IST)."
    elif minutes_to_flat <= 0:
        phase = (
            "The NSE cash session is at or past the 15:10 IST square-off; "
            "no new entries are permitted."
        )
    else:
        phase = (
            f"The NSE cash session has been open for {minutes_since_open} minutes; "
            f"new entries stop in {minutes_to_flat} minutes (15:10 IST square-off)."
        )
    return (
        f"{clock} {phase} Treat the market as already in progress: do not assume "
        "a fresh open. Timestamps in the payloads below are UTC, not IST."
    )

# Appended to the versioned prompt when news is present, so a change to how news is
# presented is a new prompt version rather than a silent behaviour change.
NEWS_INSTRUCTION = """
News context, when present, is supplied in two clearly separated blocks:
ORIGINAL SOURCE INFORMATION (what publishers stated) and
AI NEWS INTERPRETATION (a separate model's judgement of those statements).
Treat the first as unverified claims and the second as an opinion that may be wrong.
Analyst confidence describes confidence in an interpretation; it is not a trading
confidence and must never be used as one. Weigh news against the live tick and the
current book. News is sufficient reason to consider opening, adding to, reducing, or
closing a position, and equally sufficient reason to do nothing. Decide independently.
"""


# Fallback limits text for direct construction without settings (tests, probes).
# Production call sites render the live caps from settings instead.
_DEFAULT_POSITION_LIMITS = (
    "at most 50000 INR total AI capital, at most 25000 INR in any single "
    "position, at most 50000 INR gross exposure, at most 5 open positions"
)


def position_limits_text(
    settings: Settings, *, capital_cap_disabled: bool = False, position_count_cap_disabled: bool = False
) -> str:
    """Render the live paper-account caps for the manager prompt."""
    if capital_cap_disabled and position_count_cap_disabled:
        return (
            "no fixed AI capital, single-position, or gross-exposure cap and no "
            "fixed limit on the number of open positions; size each position on "
            "your own judgement within the paper account's available cash"
        )
    if capital_cap_disabled:
        return (
            "no fixed AI capital, single-position, or gross-exposure cap; size each "
            "position on your own judgement within the paper account's available cash, "
            f"with at most {settings.max_open_positions} open positions"
        )
    if position_count_cap_disabled:
        return (
            f"use at most {settings.max_ai_capital_inr:.0f} INR total AI capital, "
            f"at most {settings.max_single_position_inr:.0f} INR in any single position, "
            f"at most {settings.max_gross_exposure_inr:.0f} INR gross exposure, with no "
            "fixed limit on the number of open positions; a buy breaching a "
            "money cap is denied"
        )
    return (
        f"use at most {settings.max_ai_capital_inr:.0f} INR total AI capital, "
        f"at most {settings.max_single_position_inr:.0f} INR in any single position, "
        f"at most {settings.max_gross_exposure_inr:.0f} INR gross exposure, and "
        f"at most {settings.max_open_positions} open positions; a buy breaching a "
        "money cap is denied"
    )


@dataclass(frozen=True)
class OpenPositionBrief:
    """One live position, pre-digested for the exit review.

    The manager sees raw books (symbol, quantity, average entry) plus a board
    of live prices, which forces it to recompute winners from scratch every
    review and biases it toward hunting entries. This block does the arithmetic
    up front so the exit decision is explicit: bank, reduce, or hold with a
    fresh reason. Unrealized figures are gross of exit charges; the prompt
    tells the manager to judge against economics net of them.
    """

    symbol: str
    quantity: int
    average_entry_price: Decimal
    last_price: Decimal | None
    unrealized_inr: Decimal | None
    unrealized_pct: Decimal | None
    held_minutes: int | None
    target_price: Decimal | None
    invalidation_price: Decimal | None
    entry_thesis: str | None


def render_open_position_brief(positions: list[OpenPositionBrief]) -> str:
    """Prompt block listing live positions for the exit-first review."""
    if not positions:
        return ""
    lines = ["Open positions (yours — review each for exit BEFORE hunting new entries):"]
    for position in positions:
        if position.last_price is None or position.unrealized_inr is None:
            mark = "no live mark; exit only on a fresh price, never blind"
        else:
            pct = (
                f"{position.unrealized_pct:+.2f}%"
                if position.unrealized_pct is not None
                else "n/a"
            )
            mark = (
                f"LTP={position.last_price:.2f} "
                f"unrealized={position.unrealized_inr:+.2f} INR ({pct})"
            )
        age = (
            f"held {position.held_minutes}m"
            if position.held_minutes is not None
            else "held age unknown"
        )
        plan_bits = []
        if position.target_price is not None:
            plan_bits.append(f"target {position.target_price:.2f}")
        if position.invalidation_price is not None:
            plan_bits.append(f"invalidation {position.invalidation_price:.2f}")
        plan = "; ".join(plan_bits) if plan_bits else "no recorded entry plan"
        thesis = (position.entry_thesis or "").strip()
        if len(thesis) > 200:
            thesis = thesis[:197] + "..."
        lines.append(
            f"  {position.symbol}: LONG {position.quantity} @ avg {position.average_entry_price:.2f}; "
            f"{mark}; {age}; entry plan: {plan}"
            + (f"; thesis: {thesis}" if thesis else "")
        )
    return "\n".join(lines) + "\n"


class PortfolioManagerAgent:
    def __init__(
        self,
        provider: AIProvider,
        prompt_version: str = "v1",
        *,
        position_limits: str | None = None,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        template = (
            Path(__file__).parent / "prompts" / f"portfolio_manager_{prompt_version}.txt"
        ).read_text()
        # Prompt files may reference {position_limits}; values are data, so the
        # template version stays comparable while caps come from settings.
        self._template = template
        self._apply_position_limits(position_limits or _DEFAULT_POSITION_LIMITS)

    def _apply_position_limits(self, text: str) -> None:
        self.prompt = self._template.format(position_limits=text)
        # Only used when a bundle is supplied, so the no-news path is unchanged.
        self.prompt_with_news = self.prompt + NEWS_INSTRUCTION

    def set_position_limits(self, text: str) -> None:
        """Re-render the prompt after the operator changes the money caps."""
        self._apply_position_limits(text)

    async def review(
        self,
        tick: MarketTick,
        portfolio: PortfolioState,
        news: NewsReviewBundle | None = None,
        *,
        board: list[MarketState] | None = None,
        position_brief: str = "",
        bars: Mapping[str, tuple[Candle, ...]] | None = None,
    ) -> tuple[PortfolioDecision, ProviderMetadata]:
        return await self._complete(
            self._context(
                tick,
                portfolio,
                news,
                board=board,
                position_brief=position_brief,
                bars=bars,
            ),
            prompt=self._select_prompt(news),
        )

    async def reconsider(
        self,
        tick: MarketTick,
        portfolio: PortfolioState,
        critique: str,
        news: NewsReviewBundle | None = None,
    ) -> tuple[PortfolioDecision, ProviderMetadata]:
        extra = f"\nCritic feedback: {critique}\n"
        news_block = self._news_block(news, portfolio)
        return await self._complete(
            f"Market tick: {tick.model_dump_json()}\n"
            f"Portfolio: {portfolio.model_dump_json()}\n"
            f"{news_block}{extra}Return a new validated portfolio decision.",
            prompt=self._select_prompt(news),
        )

    def _select_prompt(self, news: NewsReviewBundle | None) -> str:
        if news is not None and not news.is_empty:
            return self.prompt_with_news
        return self.prompt

    def _news_block(self, news: NewsReviewBundle | None, portfolio: PortfolioState) -> str:
        if news is None or news.is_empty:
            return ""
        # Held symbols come from the paper book the AI already has, so the prompt can
        # mark news that concerns a live position without the agent knowing the broker.
        held = {position.symbol.upper() for position in portfolio.positions if position.quantity}
        return news.as_prompt(held_symbols=held) + "\n"

    def _context(
        self,
        tick: MarketTick,
        portfolio: PortfolioState,
        news: NewsReviewBundle | None,
        *,
        board: list[MarketState] | None = None,
        position_brief: str = "",
        bars: Mapping[str, tuple[Candle, ...]] | None = None,
    ) -> str:
        board_block = self._board_block(board, bars=bars)
        brief_block = f"{position_brief}" if position_brief else ""
        return (
            f"{market_clock(tick.exchange_timestamp)}\n"
            f"Market tick (timestamps UTC): {tick.model_dump_json()}\n"
            f"Portfolio: {portfolio.model_dump_json()}\n"
            f"{brief_block}"
            f"{board_block}"
            f"{self._news_block(news, portfolio)}"
            "Return only the validated portfolio decision schema."
        )

    @staticmethod
    def _board_block(
        board: list[MarketState] | None,
        *,
        bars: Mapping[str, tuple[Candle, ...]] | None = None,
    ) -> str:
        """Compact one-line-per-symbol board so the AI can pick its own symbol.

        Each line carries live price plus, when history is available, the last
        few 5-minute bars as high/low/close and a real ATR. Without them the
        model can only guess at levels and trends. A deterministic screener
        ranks the board first; the top names carry a star and are repeated as a
        shortlist hint.
        """
        if not board:
            return ""
        from ai_trader.screening.indicators import atr
        from ai_trader.screening.screener import rank_candidates

        series_by_symbol = bars or {}
        shortlist = {candidate.symbol for candidate in rank_candidates(board)}
        lines = [
            "Watchlist (live FYERS prices, one line per symbol). "
            "5m= the most recent 5-minute bars as H/L/C, oldest first; "
            "ATR5= average true range of those bars in rupees."
        ]
        for state in board:
            candle = state.current_1m_candle
            ohlc = (
                f" O={candle.open} H={candle.high} L={candle.low} C={candle.close}"
                f" V={candle.volume}"
                if candle is not None
                else ""
            )
            prev = f" prev_close={state.previous_close}" if state.previous_close is not None else ""
            series = series_by_symbol.get(state.instrument.symbol.upper())
            atr_value = atr(list(series), 14) if series else None
            if atr_value is not None:
                volatility = f" ATR5={atr_value.quantize(Decimal('0.01'))}"
                history = " 5m=" + ",".join(
                    f"{c.high.quantize(Decimal('0.01'))}/"
                    f"{c.low.quantize(Decimal('0.01'))}/"
                    f"{c.close.quantize(Decimal('0.01'))}"
                    for c in series
                )
            else:
                history = ""
                if candle is not None and state.previous_close:
                    # No bar history: fall back to the session range and say so,
                    # rather than presenting a range as if it were an ATR.
                    day_range = candle.high - candle.low
                    if day_range > 0:
                        range_pct = day_range / state.previous_close * Decimal(100)
                        volatility = (
                            f" range={day_range.quantize(Decimal('0.01'))}"
                            f" ({range_pct.quantize(Decimal('0.01'))}%)"
                        )
                    else:
                        volatility = ""
                else:
                    volatility = ""
            star = " ★" if state.instrument.symbol in shortlist else ""
            lines.append(
                f"  {state.instrument.symbol}: LTP={state.last_price}{prev}{ohlc}"
                f"{volatility}{history} as_of={state.timestamp.astimezone(IST):%H:%M:%S} IST{star}"
            )
        lines.append(
            "Survey every symbol on the watchlist, not only the starred ones, and"
            " propose at most one position in the single best symbol, or hold/stay in cash."
            " State quantity_shares explicitly."
        )
        if shortlist:
            lines.append(
                "Screener hint (starred: strongest intraday signals this review, ten per board): "
                + ", ".join(sorted(shortlist))
                + ". A hint only — any unstarred symbol is a valid pick if its setup is better."
            )
        return "\n".join(lines) + "\n"

    async def _complete(
        self, context: str, *, prompt: str
    ) -> tuple[PortfolioDecision, ProviderMetadata]:
        return await self.provider.structured_completion(
            prompt,
            context,
            PortfolioDecision,
            prompt_name="portfolio_manager",
            prompt_version=self.prompt_version,
        )
