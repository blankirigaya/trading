"""Configured news sources.

Secondary media sources are real, public RSS feeds. Trust is set here as
configuration; the AI never assigns trust.
"""

from ai_trader.domain.news import NewsCategory, NewsSource, SourceType, TrustLevel
from ai_trader.news.official import OfficialListingHTMLProvider, official_sources
from ai_trader.news.provider import GenericRSSProvider, NewsProvider

SECONDARY_SOURCES: list[NewsSource] = [
    NewsSource(
        source_id="livemint-markets",
        name="LiveMint Markets",
        homepage="https://www.livemint.com/rss/markets",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    ),
    NewsSource(
        source_id="livemint-companies",
        name="LiveMint Companies",
        homepage="https://www.livemint.com/rss/companies",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.COMPANY_NEWS,
    ),
    NewsSource(
        source_id="et-top-stories",
        name="Economic Times Top Stories",
        homepage="https://economictimes.indiatimes.com/rssfeedstopstories.cms",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    ),
    NewsSource(
        source_id="bs-markets",
        name="Business Standard Markets",
        homepage="https://www.business-standard.com/rss/markets-106.rss",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    ),
]

AGGREGATOR_SOURCES: list[NewsSource] = [
    NewsSource(
        source_id="rss-configured-1",
        name="Configured RSS provider 1",
        homepage="https://feeds.example.com/markets.xml",
        source_type=SourceType.AGGREGATOR,
        trust_level=TrustLevel.UNKNOWN,
        enabled=False,
    ),
]


def all_sources() -> list[NewsSource]:
    return [*official_sources(), *SECONDARY_SOURCES, *AGGREGATOR_SOURCES]


def enabled_sources() -> list[NewsSource]:
    return [source for source in all_sources() if source.enabled]


def build_provider(source: NewsSource, timeout: int = 25) -> NewsProvider:
    """Return the provider implementation appropriate for a source."""
    if source.source_type is SourceType.OFFICIAL:
        return OfficialListingHTMLProvider(source, timeout=timeout)
    return GenericRSSProvider(source, timeout=timeout)


def source_by_id(source_id: str) -> NewsSource | None:
    for source in all_sources():
        if source.source_id == source_id:
            return source
    return None
