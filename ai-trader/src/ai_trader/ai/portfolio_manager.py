from pathlib import Path

from ai_trader.ai.base import AIProvider
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioDecision, ProviderMetadata
from ai_trader.domain.market import MarketState, MarketTick
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.news.review_context import NewsReviewBundle

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
    ) -> tuple[PortfolioDecision, ProviderMetadata]:
        return await self._complete(
            self._context(tick, portfolio, news, board=board), prompt=self._select_prompt(news)
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
    ) -> str:
        board_block = self._board_block(board)
        return (
            f"Market tick: {tick.model_dump_json()}\n"
            f"Portfolio: {portfolio.model_dump_json()}\n"
            f"{board_block}"
            f"{self._news_block(news, portfolio)}"
            "Return only the validated portfolio decision schema."
        )

    @staticmethod
    def _board_block(board: list[MarketState] | None) -> str:
        """Compact one-line-per-symbol board so the AI can pick its own symbol.

        A deterministic screener ranks the board first; the top names carry a
        star and are repeated as a shortlist. The AI focuses there but may
        overrule with written justification.
        """
        if not board:
            return ""
        from ai_trader.screening.screener import rank_candidates

        shortlist = {candidate.symbol for candidate in rank_candidates(board)}
        lines = ["Watchlist (live FYERS prices, one line per symbol):"]
        for state in board:
            candle = state.current_1m_candle
            ohlc = (
                f" O={candle.open} H={candle.high} L={candle.low} C={candle.close}"
                f" V={candle.volume}"
                if candle is not None
                else ""
            )
            prev = f" prev_close={state.previous_close}" if state.previous_close is not None else ""
            star = " ★" if state.instrument.symbol in shortlist else ""
            lines.append(
                f"  {state.instrument.symbol}: LTP={state.last_price}{prev}{ohlc}"
                f" as_of={state.timestamp.isoformat()}{star}"
            )
        lines.append(
            "Survey the whole board and propose at most one position in the single"
            " best symbol, or hold/stay in cash. State quantity_shares explicitly."
        )
        if shortlist:
            lines.append(
                "Screener shortlist (starred: strongest intraday signals this review): "
                + ", ".join(sorted(shortlist))
                + ". Start here; overrule only with a written, number-backed reason."
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
