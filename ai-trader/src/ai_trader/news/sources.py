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
    # Tier-1 desks: faster on market-moving flow than the dailies above.
    # Moneycontrol is deliberately absent: its feeds reject automated
    # clients (HTTP 403), and we do not spoof browser identities to dodge
    # a publisher's bot protection. Business Today covers the same lane.
    NewsSource(
        source_id="cnbc-tv18-markets",
        name="CNBC-TV18 Markets",
        homepage="https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    ),
    NewsSource(
        source_id="ndtv-profit-latest",
        name="NDTV Profit Latest",
        homepage="https://feeds.feedburner.com/ndtvprofit-latest",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    ),
    NewsSource(
        source_id="business-today-markets",
        name="Business Today Markets",
        homepage="https://www.businesstoday.in/rssfeeds/?id=home",
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


#: Tier-1 desks, separately switchable at runtime so a noisy or expensive
#: feed can be killed without touching the rest of the news pipeline.
TIER1_SOURCE_IDS: frozenset[str] = frozenset(
    {"cnbc-tv18-markets", "ndtv-profit-latest", "business-today-markets"}
)


def enabled_sources(*, include_tier1: bool = True) -> list[NewsSource]:
    """Sources eligible for continuous ingestion.

    ``include_tier1=False`` drops the Tier-1 desks (persisted operator
    override); official filings and secondary dailies are unaffected.
    """
    return [
        source
        for source in all_sources()
        if source.enabled and (include_tier1 or source.source_id not in TIER1_SOURCE_IDS)
    ]


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
