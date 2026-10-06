"""Multi-source news intelligence.

Sources provide information only. Interpretation is the AI's job, and trading
judgment stays with the Portfolio Manager AI and Jev.
"""

from ai_trader.news.clustering import NewsClusterIndex
from ai_trader.news.dedup import (
    canonical_url_key,
    content_hash_for,
    headline_hash_for,
    normalize_headline,
)
from ai_trader.news.documents import (
    DocumentPolicyError,
    discover_document_urls,
    download_document,
    extract_text,
    host_allowed,
    is_trusted_source,
)
from ai_trader.news.official import OfficialListingHTMLProvider, official_sources
from ai_trader.news.pipeline import NewsPipeline, PipelineStats, poll_sources
from ai_trader.news.provider import GenericRSSProvider, NewsProvider, ProviderResult
from ai_trader.news.service import NewsIntelligence, PollSummary
from ai_trader.news.sources import all_sources, build_provider, enabled_sources, source_by_id

__all__ = [
    "DocumentPolicyError",
    "GenericRSSProvider",
    "NewsClusterIndex",
    "NewsIntelligence",
    "NewsPipeline",
    "NewsProvider",
    "OfficialListingHTMLProvider",
    "PipelineStats",
    "PollSummary",
    "ProviderResult",
    "all_sources",
    "build_provider",
    "canonical_url_key",
    "content_hash_for",
    "download_document",
    "discover_document_urls",
    "enabled_sources",
    "extract_text",
    "headline_hash_for",
    "host_allowed",
    "is_trusted_source",
    "normalize_headline",
    "official_sources",
    "poll_sources",
    "source_by_id",
]
