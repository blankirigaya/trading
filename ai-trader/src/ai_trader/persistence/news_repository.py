"""News repository: translates between ORM rows and domain models."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ai_trader.domain.news import (
    AnalysisStatus,
    Direction,
    Horizon,
    Materiality,
    NewsAnalysis,
    NewsCategory,
    NewsDocument,
    NewsEventCluster,
    NewsItem,
    NewsSource,
    SourceType,
    TrustLevel,
)
from ai_trader.persistence.news_models import (
    NewsAnalysisRow,
    NewsClusterMemberRow,
    NewsDocumentRow,
    NewsEventClusterRow,
    NewsItemRow,
    NewsSourceRow,
    NewsSymbolLinkRow,
)

LINK_FROM_ITEM = "FROM_ITEM"
LINK_FROM_ANALYSIS = "FROM_ANALYSIS"


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class NewsRepository:
    """All news persistence lives behind this boundary."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- sources -----------------------------------------------------------
    def upsert_source(self, source: NewsSource) -> None:
        with Session(self.engine) as session:
            row = session.get(NewsSourceRow, source.source_id)
            values = {
                "name": source.name,
                "homepage": source.homepage,
                "source_type": source.source_type.value,
                "trust_level": source.trust_level.value,
                "default_category": source.default_category.value,
                "enabled": source.enabled,
                "poll_interval_seconds": source.poll_interval_seconds,
                "allowed_document_hosts": list(source.allowed_document_hosts),
                "notes": source.notes,
            }
            if row is None:
                session.add(
                    NewsSourceRow(
                        source_id=source.source_id, created_at=datetime.now(UTC), **values
                    )
                )
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            session.commit()

    def list_sources(self) -> list[NewsSource]:
        with Session(self.engine) as session:
            rows = session.scalars(select(NewsSourceRow).order_by(NewsSourceRow.source_id)).all()
            return [self._to_source(row) for row in rows]

    def _to_source(self, row: NewsSourceRow) -> NewsSource:
        return NewsSource(
            source_id=row.source_id,
            name=row.name,
            homepage=row.homepage,
            source_type=SourceType(row.source_type),
            trust_level=TrustLevel(row.trust_level),
            default_category=NewsCategory(row.default_category),
            enabled=row.enabled,
            poll_interval_seconds=row.poll_interval_seconds,
            allowed_document_hosts=tuple(row.allowed_document_hosts or ()),
            notes=row.notes,
        )

    # --- items -------------------------------------------------------------
    def save_item(self, item: NewsItem, cluster_id: str) -> bool:
        """Store an item. Returns False when the item_id already exists."""
        with Session(self.engine) as session:
            if session.get(NewsItemRow, item.item_id) is not None:
                return False
            session.add(
                NewsItemRow(
                    item_id=item.item_id,
                    source_id=item.source_id,
                    source_name=item.source_name,
                    source_type=item.source_type.value,
                    trust_level=item.trust_level.value,
                    source_native_id=item.source_native_id,
                    headline=item.headline,
                    canonical_url=item.canonical_url,
                    url=item.url,
                    published_at=_as_aware(item.published_at),
                    received_at=_as_aware(item.received_at),
                    category=item.category.value,
                    company=item.company,
                    content_hash=item.content_hash,
                    headline_hash=item.headline_hash,
                    excerpt=item.excerpt,
                    content=item.content,
                    raw_metadata=dict(item.raw_metadata),
                    cluster_id=cluster_id,
                    version=item.version,
                    supersedes_item_id=item.supersedes_item_id,
                )
            )
            for symbol in item.symbols:
                session.add(
                    NewsSymbolLinkRow(
                        item_id=item.item_id,
                        symbol=symbol,
                        link_type=LINK_FROM_ITEM,
                        created_at=datetime.now(UTC),
                    )
                )
            session.commit()
            return True

    def _to_item(self, row: NewsItemRow) -> NewsItem:
        return NewsItem(
            item_id=row.item_id,
            source_id=row.source_id,
            source_name=row.source_name,
            source_type=SourceType(row.source_type),
            trust_level=TrustLevel(row.trust_level),
            source_native_id=row.source_native_id,
            headline=row.headline,
            canonical_url=row.canonical_url,
            url=row.url,
            published_at=_as_aware(row.published_at),
            received_at=_as_aware(row.received_at),
            category=NewsCategory(row.category),
            company=row.company,
            content_hash=row.content_hash,
            headline_hash=row.headline_hash,
            excerpt=row.excerpt,
            content=row.content,
            raw_metadata=dict(row.raw_metadata or {}),
            cluster_id=row.cluster_id,
            version=row.version or 1,
            supersedes_item_id=row.supersedes_item_id,
        )

    def latest_version_for(self, source_id: str, canonical_url: str) -> NewsItem | None:
        """The most recent stored version of an item identified at its source.

        Used to recognise a publisher correction: same source and canonical URL but a
        different content hash means the item was revised, not repeated.
        """
        with Session(self.engine) as session:
            row = session.scalar(
                select(NewsItemRow)
                .where(
                    NewsItemRow.source_id == source_id,
                    NewsItemRow.canonical_url == canonical_url,
                )
                .order_by(NewsItemRow.version.desc())
            )
            return self._to_item(row) if row else None

    def get_item(self, item_id: str) -> NewsItem | None:
        with Session(self.engine) as session:
            row = session.get(NewsItemRow, item_id)
            return self._to_item(row) if row else None

    def recent_items(self, limit: int = 100) -> list[NewsItem]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsItemRow).order_by(NewsItemRow.published_at.desc()).limit(limit)
            ).all()
            return [self._to_item(row) for row in rows]

    def items_for_symbol(self, symbol: str, limit: int = 50) -> list[NewsItem]:
        """Every item linked to the symbol, whether the link came from the item or the AI."""
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsItemRow)
                .join(NewsSymbolLinkRow, NewsSymbolLinkRow.item_id == NewsItemRow.item_id)
                .where(NewsSymbolLinkRow.symbol == symbol)
                .order_by(NewsItemRow.published_at.desc())
                .distinct()
                .limit(limit)
            ).all()
            return [self._to_item(row) for row in rows]

    def symbol_links(self, item_id: str) -> list[str]:
        """Link provenance for an item, so the UI can show why a symbol was attached."""
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsSymbolLinkRow)
                .where(NewsSymbolLinkRow.item_id == item_id)
                .order_by(NewsSymbolLinkRow.symbol)
            ).all()
            return [f"{row.symbol}:{row.link_type}" for row in rows]

    # --- clusters ----------------------------------------------------------
    def save_cluster(self, cluster: NewsEventCluster) -> None:
        with Session(self.engine) as session:
            row = session.get(NewsEventClusterRow, cluster.cluster_id)
            values = {
                "canonical_headline": cluster.canonical_headline,
                "representative_item_id": cluster.representative_item_id,
                "first_seen_at": _as_aware(cluster.first_seen_at),
                "last_seen_at": _as_aware(cluster.last_seen_at),
                "symbols": list(cluster.symbols),
                "highest_trust_level": cluster.highest_trust_level.value,
                "member_count": len(cluster.member_item_ids),
                "duplicate_count": cluster.duplicate_count,
            }
            if row is None:
                session.add(
                    NewsEventClusterRow(
                        cluster_id=cluster.cluster_id, created_at=datetime.now(UTC), **values
                    )
                )
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            session.commit()

    def _to_cluster(self, row: NewsEventClusterRow) -> NewsEventCluster:
        return NewsEventCluster(
            cluster_id=row.cluster_id,
            canonical_headline=row.canonical_headline,
            representative_item_id=row.representative_item_id,
            member_item_ids=(),
            first_seen_at=_as_aware(row.first_seen_at),
            last_seen_at=_as_aware(row.last_seen_at),
            symbols=tuple(row.symbols or ()),
            highest_trust_level=TrustLevel(row.highest_trust_level),
            duplicate_count=row.duplicate_count,
        )

    def add_cluster_member(
        self, cluster_id: str, item_id: str, source_id: str, is_representative: bool
    ) -> None:
        with Session(self.engine) as session:
            session.merge(
                NewsClusterMemberRow(
                    cluster_id=cluster_id,
                    item_id=item_id,
                    source_id=source_id,
                    added_at=datetime.now(UTC),
                    is_representative=is_representative,
                )
            )
            session.commit()

    def cluster_members(self, cluster_id: str) -> list[str]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsClusterMemberRow).where(NewsClusterMemberRow.cluster_id == cluster_id)
            ).all()
            return [row.item_id for row in rows]

    # --- documents ---------------------------------------------------------
    def save_document(self, document: NewsDocument, item_id: str | None = None) -> None:
        with Session(self.engine) as session:
            session.merge(
                NewsDocumentRow(
                    document_id=document.document_id,
                    item_id=item_id,
                    url=document.url,
                    sha256=document.sha256,
                    content_type=document.content_type,
                    byte_size=document.byte_size,
                    downloaded_at=_as_aware(document.downloaded_at),
                    local_path=document.local_path,
                    extracted_text=document.extracted_text,
                    text_truncated=document.text_truncated,
                )
            )
            session.commit()

    def get_document(self, document_id: str) -> NewsDocument | None:
        with Session(self.engine) as session:
            row = session.get(NewsDocumentRow, document_id)
            if row is None:
                return None
            return NewsDocument(
                document_id=row.document_id,
                url=row.url,
                sha256=row.sha256,
                content_type=row.content_type,
                byte_size=row.byte_size,
                downloaded_at=_as_aware(row.downloaded_at),
                local_path=row.local_path,
                extracted_text=row.extracted_text,
                text_truncated=row.text_truncated,
            )

    # --- analyses ----------------------------------------------------------
    def save_analysis(self, analysis: NewsAnalysis) -> None:
        with Session(self.engine) as session:
            values = {
                "cluster_id": analysis.cluster_id,
                "item_id": analysis.item_id,
                "status": analysis.status.value,
                "event_type": analysis.event_type,
                "summary": analysis.summary,
                "affected_symbols": list(analysis.affected_symbols),
                "materiality": analysis.materiality.value if analysis.materiality else None,
                "direction": analysis.direction.value if analysis.direction else None,
                "horizon": analysis.horizon.value if analysis.horizon else None,
                "confidence": analysis.confidence,
                "requires_portfolio_review": analysis.requires_portfolio_review,
                "evidence": list(analysis.evidence),
                "uncertainties": list(analysis.uncertainties),
                "analysis_started_at": (
                    _as_aware(analysis.analysis_started_at)
                    if analysis.analysis_started_at
                    else None
                ),
                "analysis_completed_at": (
                    _as_aware(analysis.analysis_completed_at)
                    if analysis.analysis_completed_at
                    else None
                ),
                "portfolio_review_at": (
                    _as_aware(analysis.portfolio_review_at)
                    if analysis.portfolio_review_at
                    else None
                ),
                "portfolio_review_started_at": (
                    _as_aware(analysis.portfolio_review_started_at)
                    if analysis.portfolio_review_started_at
                    else None
                ),
                "portfolio_decision_at": (
                    _as_aware(analysis.portfolio_decision_at)
                    if analysis.portfolio_decision_at
                    else None
                ),
                "paper_order_at": (
                    _as_aware(analysis.paper_order_at) if analysis.paper_order_at else None
                ),
                "news_decision_id": analysis.news_decision_id,
                "failure_reason": analysis.failure_reason,
                "provider_metadata": dict(analysis.provider_metadata),
            }
            row = session.get(NewsAnalysisRow, analysis.analysis_id)
            if row is None:
                session.add(NewsAnalysisRow(analysis_id=analysis.analysis_id, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            for symbol in analysis.affected_symbols:
                session.merge(
                    NewsSymbolLinkRow(
                        item_id=analysis.item_id,
                        symbol=symbol,
                        link_type=LINK_FROM_ANALYSIS,
                        created_at=datetime.now(UTC),
                    )
                )
            session.commit()

    def _to_analysis(self, row: NewsAnalysisRow) -> NewsAnalysis:
        return NewsAnalysis(
            analysis_id=row.analysis_id,
            cluster_id=row.cluster_id,
            item_id=row.item_id,
            status=AnalysisStatus(row.status),
            event_type=row.event_type,
            summary=row.summary,
            affected_symbols=tuple(row.affected_symbols or ()),
            materiality=Materiality(row.materiality) if row.materiality else None,
            direction=Direction(row.direction) if row.direction else None,
            horizon=Horizon(row.horizon) if row.horizon else None,
            confidence=row.confidence,
            requires_portfolio_review=row.requires_portfolio_review,
            evidence=tuple(row.evidence or ()),
            uncertainties=tuple(row.uncertainties or ()),
            analysis_started_at=(
                _as_aware(row.analysis_started_at) if row.analysis_started_at else None
            ),
            analysis_completed_at=(
                _as_aware(row.analysis_completed_at) if row.analysis_completed_at else None
            ),
            portfolio_review_at=(
                _as_aware(row.portfolio_review_at) if row.portfolio_review_at else None
            ),
            portfolio_review_started_at=(
                _as_aware(row.portfolio_review_started_at)
                if row.portfolio_review_started_at
                else None
            ),
            portfolio_decision_at=(
                _as_aware(row.portfolio_decision_at) if row.portfolio_decision_at else None
            ),
            paper_order_at=_as_aware(row.paper_order_at) if row.paper_order_at else None,
            news_decision_id=row.news_decision_id,
            failure_reason=row.failure_reason,
            provider_metadata=dict(row.provider_metadata or {}),
        )

    def get_analysis(self, analysis_id: str) -> NewsAnalysis | None:
        with Session(self.engine) as session:
            row = session.get(NewsAnalysisRow, analysis_id)
            return self._to_analysis(row) if row else None

    def analyses_for_cluster(self, cluster_id: str) -> list[NewsAnalysis]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsAnalysisRow).where(NewsAnalysisRow.cluster_id == cluster_id)
            ).all()
            return [self._to_analysis(row) for row in rows]

    def latest_analysis_for_item(self, item_id: str) -> NewsAnalysis | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(NewsAnalysisRow)
                .where(NewsAnalysisRow.item_id == item_id)
                .order_by(NewsAnalysisRow.analysis_started_at.desc().nullslast())
            )
            return self._to_analysis(row) if row else None

    def recent_analyses(self, limit: int = 100) -> list[NewsAnalysis]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(NewsAnalysisRow)
                .order_by(NewsAnalysisRow.analysis_completed_at.desc().nullslast())
                .limit(limit)
            ).all()
            return [self._to_analysis(row) for row in rows]

    def mark_reviewed(self, analysis_id: str, when: datetime | None = None) -> None:
        with Session(self.engine) as session:
            row = session.get(NewsAnalysisRow, analysis_id)
            if row is not None:
                row.portfolio_review_at = when or datetime.now(UTC)
                session.commit()

    def record_latency(
        self,
        analysis_id: str,
        *,
        review_started_at: datetime | None = None,
        decision_at: datetime | None = None,
        paper_order_at: datetime | None = None,
        decision_id: str | None = None,
    ) -> None:
        """Append to the news-to-trade latency chain as each stage completes."""
        with Session(self.engine) as session:
            row = session.get(NewsAnalysisRow, analysis_id)
            if row is None:
                return
            if review_started_at is not None:
                row.portfolio_review_started_at = _as_aware(review_started_at)
            if decision_at is not None:
                row.portfolio_decision_at = _as_aware(decision_at)
            if paper_order_at is not None:
                row.paper_order_at = _as_aware(paper_order_at)
            if decision_id is not None:
                row.news_decision_id = decision_id
            session.commit()

    def latency_rows(self, limit: int = 50) -> list[dict[str, object]]:
        """Measured news-to-trade latencies, newest first."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(NewsAnalysisRow, NewsItemRow)
                .join(NewsItemRow, NewsItemRow.item_id == NewsAnalysisRow.item_id)
                .where(NewsAnalysisRow.status == AnalysisStatus.COMPLETED.value)
                .order_by(NewsAnalysisRow.analysis_completed_at.desc().nullslast())
                .limit(limit)
            ).all()
            return [
                news_latency(
                    published_at=_as_aware(item.published_at),
                    received_at=_as_aware(item.received_at),
                    analysis_started_at=(
                        _as_aware(analysis.analysis_started_at)
                        if analysis.analysis_started_at
                        else None
                    ),
                    analysis_completed_at=(
                        _as_aware(analysis.analysis_completed_at)
                        if analysis.analysis_completed_at
                        else None
                    ),
                    portfolio_review_started_at=(
                        _as_aware(analysis.portfolio_review_started_at)
                        if analysis.portfolio_review_started_at
                        else None
                    ),
                    portfolio_decision_at=(
                        _as_aware(analysis.portfolio_decision_at)
                        if analysis.portfolio_decision_at
                        else None
                    ),
                    paper_order_at=(
                        _as_aware(analysis.paper_order_at) if analysis.paper_order_at else None
                    ),
                    item_id=analysis.item_id,
                    analysis_id=analysis.analysis_id,
                    source_id=item.source_id,
                    symbols=analysis.affected_symbols or (),
                )
                for analysis, item in rows
            ]


def _ms(later: datetime | None, earlier: datetime | None) -> int | None:
    """Milliseconds between two timestamps, or None if either is missing.

    A negative value means the stages overlapped, which is reported rather than clamped.
    """
    if later is None or earlier is None:
        return None
    return int((later - earlier).total_seconds() * 1000)


def news_latency(
    *,
    published_at: datetime,
    received_at: datetime,
    analysis_started_at: datetime | None,
    analysis_completed_at: datetime | None,
    portfolio_review_started_at: datetime | None,
    portfolio_decision_at: datetime | None,
    paper_order_at: datetime | None,
    item_id: str,
    analysis_id: str,
    source_id: str,
    symbols: list[str] | tuple[str, ...],
) -> dict[str, object]:
    """Derive the four headline latency metrics from the recorded stage timestamps."""
    return {
        "item_id": item_id,
        "analysis_id": analysis_id,
        "source_id": source_id,
        "symbols": list(symbols),
        "source_to_ingestion_ms": _ms(received_at, published_at),
        "ingestion_to_analysis_ms": _ms(analysis_started_at, received_at),
        "analysis_to_decision_ms": _ms(portfolio_decision_at, analysis_completed_at),
        "news_to_trade_ms": _ms(paper_order_at, received_at),
        "review_latency_ms": _ms(portfolio_decision_at, portfolio_review_started_at),
        "published_at": published_at.isoformat(),
        "received_at": received_at.isoformat(),
        "analysis_started_at": analysis_started_at.isoformat() if analysis_started_at else None,
        "analysis_completed_at": (
            analysis_completed_at.isoformat() if analysis_completed_at else None
        ),
        "portfolio_review_started_at": (
            portfolio_review_started_at.isoformat() if portfolio_review_started_at else None
        ),
        "portfolio_decision_at": (
            portfolio_decision_at.isoformat() if portfolio_decision_at else None
        ),
        "paper_order_at": paper_order_at.isoformat() if paper_order_at else None,
    }
