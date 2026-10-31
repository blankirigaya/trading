"""SQLAlchemy models for the news intelligence tables.

ORM rows never leave `persistence`; the repository returns domain models.
"""

from datetime import datetime

from sqlalchemy import Index, String, Table, Text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from ai_trader.persistence.models import Base, JSONType, UTCDateTime


class NewsSourceRow(Base):
    __tablename__ = "news_sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    homepage: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    default_category: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(default=900)
    allowed_document_hosts: Mapped[list[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)


class NewsItemRow(Base):
    __tablename__ = "news_items"

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_native_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        UTCDateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime(timezone=True), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    headline_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_metadata: Mapped[dict[str, object]] = mapped_column(JSONType, default=dict)
    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Corrections: a revised item is stored as a new row that points back at the one
    # it replaces, so both versions remain auditable.
    version: Mapped[int] = mapped_column(default=1)
    supersedes_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("ix_news_items_source_published", "source_id", "published_at"),
        Index("ix_news_items_cluster", "cluster_id"),
        Index("ix_news_items_source_identity", "source_id", "canonical_url"),
    )


class NewsEventClusterRow(Base):
    __tablename__ = "news_event_clusters"

    cluster_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_headline: Mapped[str] = mapped_column(Text, nullable=False)
    representative_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    symbols: Mapped[list[str]] = mapped_column(JSONType, default=list)
    highest_trust_level: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(default=1)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)


class NewsClusterMemberRow(Base):
    __tablename__ = "news_cluster_members"

    cluster_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    is_representative: Mapped[bool] = mapped_column(default=False)


class NewsDocumentRow(Base):
    __tablename__ = "news_documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(default=0)
    downloaded_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_truncated: Mapped[bool] = mapped_column(default=False)


class NewsAnalysisRow(Base):
    __tablename__ = "news_analyses"

    analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_symbols: Mapped[list[str]] = mapped_column(JSONType, default=list)
    materiality: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    horizon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    requires_portfolio_review: Mapped[bool] = mapped_column(default=False, index=True)
    evidence: Mapped[list[str]] = mapped_column(JSONType, default=list)
    uncertainties: Mapped[list[str]] = mapped_column(JSONType, default=list)
    analysis_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(timezone=True), nullable=True
    )
    analysis_completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(timezone=True), nullable=True
    )
    portfolio_review_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(timezone=True), nullable=True
    )
    # Full news-to-trade latency chain, recorded as it happens.
    portfolio_review_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(timezone=True), nullable=True
    )
    portfolio_decision_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(timezone=True), nullable=True
    )
    paper_order_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True), nullable=True)
    news_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSONType, default=dict)


class NewsSymbolLinkRow(Base):
    __tablename__ = "news_symbol_links"

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    # Part of the key so provenance survives: an item can be linked to a symbol by
    # both the publisher and the AI without one overwriting the other.
    link_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_news_symbol_links_symbol", "symbol"),)


def news_table_objects() -> list[Table]:
    """Every news table, for schema creation and migration checks."""
    return [table for table in Base.metadata.tables.values() if table.name.startswith("news_")]


def ensure_news_tables(engine: Engine) -> None:
    """Create the news tables if they are absent.

    Alembic owns schema changes; this exists so a fresh SQLite database (tests, local
    runs) can start a session without a separate migration step.
    """
    Base.metadata.create_all(engine, tables=news_table_objects())
