from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

JSONType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONType)


class DecisionRow(Base):
    __tablename__ = "ai_decisions"
    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    input_context_hash: Mapped[str] = mapped_column(String(64))
    initial_proposal: Mapped[dict[str, object]] = mapped_column(JSONType)
    final_decision: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSONType)
    jev_critique: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    jev_provider_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    orders_generated: Mapped[list[dict[str, object]]] = mapped_column(JSONType, default=list)
    portfolio_before: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    portfolio_after: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    instrument_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    market_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_server: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    jev_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    jev_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reconsideration_requested: Mapped[bool] = mapped_column(default=False)
    reconsideration_performed: Mapped[bool] = mapped_column(default=False)
    safety_allowed: Mapped[bool | None] = mapped_column(nullable=True)
    safety_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CritiqueRow(Base):
    __tablename__ = "ai_critiques"
    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    critique: Mapped[dict[str, object]] = mapped_column(JSONType)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSONType)


class OrderRow(Base):
    __tablename__ = "orders"
    broker_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    decision_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONType)


class FillRow(Base):
    __tablename__ = "fills"
    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONType)


class PositionRow(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int]
    average_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GenericPayloadRow(Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NewsRow(GenericPayloadRow):
    __tablename__ = "news"


class AnnouncementRow(GenericPayloadRow):
    __tablename__ = "announcements"


class PortfolioSnapshotRow(GenericPayloadRow):
    __tablename__ = "portfolio_snapshots"


class DecisionOutcomeRow(GenericPayloadRow):
    __tablename__ = "decision_outcomes"


class CompanyMemoryRow(GenericPayloadRow):
    __tablename__ = "company_memory"
    content: Mapped[str] = mapped_column(Text)


# News intelligence tables are declared in a sibling module but must be part of the
# same metadata so `create_all` and Alembic see one consistent schema.
from ai_trader.persistence import news_models as _news_models  # noqa: E402,F401
