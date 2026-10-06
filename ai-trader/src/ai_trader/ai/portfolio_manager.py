from pathlib import Path

from ai_trader.ai.base import AIProvider
from ai_trader.domain.decisions import PortfolioDecision, ProviderMetadata
from ai_trader.domain.market import MarketTick
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


class PortfolioManagerAgent:
    def __init__(self, provider: AIProvider, prompt_version: str = "v1") -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self.prompt = (
            Path(__file__).parent / "prompts" / f"portfolio_manager_{prompt_version}.txt"
        ).read_text()
        # Only used when a bundle is supplied, so the no-news path is unchanged.
        self.prompt_with_news = self.prompt + NEWS_INSTRUCTION

    async def review(
        self,
        tick: MarketTick,
        portfolio: PortfolioState,
        news: NewsReviewBundle | None = None,
    ) -> tuple[PortfolioDecision, ProviderMetadata]:
        return await self._complete(
            self._context(tick, portfolio, news), prompt=self._select_prompt(news)
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
        self, tick: MarketTick, portfolio: PortfolioState, news: NewsReviewBundle | None
    ) -> str:
        return (
            f"Market tick: {tick.model_dump_json()}\n"
            f"Portfolio: {portfolio.model_dump_json()}\n"
            f"{self._news_block(news, portfolio)}"
            "Return only the validated portfolio decision schema."
        )

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
