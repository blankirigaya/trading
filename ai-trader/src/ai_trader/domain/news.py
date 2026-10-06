from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class SourceType(StrEnum):
    """Who publishes the material."""

    OFFICIAL = "OFFICIAL"
    COMPANY = "COMPANY"
    NEWS_MEDIA = "NEWS_MEDIA"
    AGGREGATOR = "AGGREGATOR"


class TrustLevel(StrEnum):
    """How much weight the source deserves. This is a source property, not an AI judgement."""

    PRIMARY = "PRIMARY"
    HIGH = "HIGH"
    SECONDARY = "SECONDARY"
    UNKNOWN = "UNKNOWN"


class NewsCategory(StrEnum):
    CORPORATE_ANNOUNCEMENT = "CORPORATE_ANNOUNCEMENT"
    RESULTS = "RESULTS"
    BOARD_MEETING = "BOARD_MEETING"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    INSIDER_TRADING = "INSIDER_TRADING"
    OFFER_DOCUMENT = "OFFER_DOCUMENT"
    REGULATORY = "REGULATORY"
    MONETARY_POLICY = "MONETARY_POLICY"
    MARKET_NEWS = "MARKET_NEWS"
    COMPANY_NEWS = "COMPANY_NEWS"
    OTHER = "OTHER"


class Materiality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"
    UNCLEAR = "UNCLEAR"


class Direction(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"


class Horizon(StrEnum):
    MINUTES = "MINUTES"
    INTRADAY = "INTRADAY"
    DAYS = "DAYS"
    WEEKS = "WEEKS"
    LONG_TERM = "LONG_TERM"
    UNCLEAR = "UNCLEAR"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class NewsSource(BaseModel):
    """A configured publisher. Trust is a configuration decision, never an AI output."""

    model_config = ConfigDict(frozen=True)
    source_id: str
    name: str
    homepage: str
    source_type: SourceType
    trust_level: TrustLevel
    default_category: NewsCategory = NewsCategory.OTHER
    enabled: bool = True
    poll_interval_seconds: int = 900
    # Only URLs under these hosts may be fetched or downloaded.
    allowed_document_hosts: tuple[str, ...] = ()
    notes: str | None = None


class NewsDocument(BaseModel):
    """A linked filing or release document, content-addressed and allowlisted."""

    model_config = ConfigDict(frozen=True)
    document_id: str
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    byte_size: int
    downloaded_at: datetime
    local_path: str | None = None
    extracted_text: str | None = None
    text_truncated: bool = False

    @field_validator("url")
    @classmethod
    def must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("document URLs must be http or https")
        return value


class NewsItem(BaseModel):
    """Source material only. Contains no AI interpretation."""

    model_config = ConfigDict(frozen=True)
    item_id: str
    source_id: str
    source_name: str
    source_type: SourceType
    trust_level: TrustLevel
    source_native_id: str | None = None
    headline: str
    canonical_url: str
    url: str
    published_at: datetime
    received_at: datetime
    category: NewsCategory
    company: str | None = None
    symbols: tuple[str, ...] = ()
    # Separate hashes: content identity vs headline identity for dedup.
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    headline_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str | None = None
    content: str | None = None
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    document_ids: tuple[str, ...] = ()
    cluster_id: str | None = None
    # A revised item is a new row that supersedes an earlier one.
    version: int = 1
    supersedes_item_id: str | None = None

    @field_validator("headline")
    @classmethod
    def headline_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("headline must not be blank")
        return value.strip()


class NewsEventCluster(BaseModel):
    """A single real-world story reported by one or more sources.

    Every source is preserved as a member; the cluster is a grouping, not a merge.
    """

    model_config = ConfigDict(frozen=True)
    cluster_id: str
    canonical_headline: str
    representative_item_id: str
    member_item_ids: tuple[str, ...] = ()
    first_seen_at: datetime
    last_seen_at: datetime
    symbols: tuple[str, ...] = ()
    highest_trust_level: TrustLevel = TrustLevel.UNKNOWN
    duplicate_count: int = 0


class NewsAnalysis(BaseModel):
    """AI interpretation of a news item or cluster. Strictly validated."""

    model_config = ConfigDict(frozen=True)
    analysis_id: str
    cluster_id: str
    item_id: str
    status: AnalysisStatus
    event_type: str | None = None
    summary: str | None = None
    affected_symbols: tuple[str, ...] = ()
    materiality: Materiality | None = None
    direction: Direction | None = None
    horizon: Horizon | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_portfolio_review: bool = False
    evidence: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    analysis_started_at: datetime | None = None
    analysis_completed_at: datetime | None = None
    portfolio_review_at: datetime | None = None
    portfolio_review_started_at: datetime | None = None
    portfolio_decision_at: datetime | None = None
    paper_order_at: datetime | None = None
    news_decision_id: str | None = None
    failure_reason: str | None = None
    provider_metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("affected_symbols", "evidence", "uncertainties", mode="before")
    @classmethod
    def coerce_sequences(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value


class NewsPortfolioReviewContext(BaseModel):
    """News context handed to the Portfolio Manager.

    Preserves source provenance and keeps news confidence separate from trading
    confidence. Contains no action instruction.
    """

    model_config = ConfigDict(frozen=True)
    cluster_id: str
    item_id: str
    analysis_id: str
    source_name: str
    source_type: SourceType
    trust_level: TrustLevel
    published_at: datetime
    headline: str
    excerpt: str | None = None
    ai_summary: str | None = None
    ai_event_type: str | None = None
    ai_materiality: Materiality | None = None
    ai_direction: Direction | None = None
    ai_horizon: Horizon | None = None
    ai_confidence: float | None = None
    ai_evidence: tuple[str, ...] = ()
    ai_uncertainties: tuple[str, ...] = ()
    requires_portfolio_review: bool = False
    related_item_count: int = 1
    symbols: tuple[str, ...] = ()


__all__ = [
    "AnalysisStatus",
    "Direction",
    "Horizon",
    "HttpUrl",
    "Materiality",
    "NewsAnalysis",
    "NewsCategory",
    "NewsDocument",
    "NewsEventCluster",
    "NewsItem",
    "NewsPortfolioReviewContext",
    "NewsSource",
    "SourceType",
    "TrustLevel",
]
