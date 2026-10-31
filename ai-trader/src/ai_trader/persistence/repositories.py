import asyncio
from decimal import Decimal
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ai_trader.domain.decisions import (
    CriticAssessment,
    DecisionStatus,
    FinalAIDecision,
    PortfolioDecision,
    ProviderMetadata,
)
from ai_trader.domain.orders import Order
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.persistence.models import Base, CritiqueRow, DecisionRow
from ai_trader.persistence.retry import with_sqlite_retry


def ensure_journal_tables(engine: Engine) -> None:
    """Create the decision journal tables if they are absent.

    Same rationale as ensure_news_tables: a fresh SQLite database (new local
    runs, tests) must be able to start a session without a migration step.
    Alembic owns schema changes; this only fills the gap for new files.
    """
    Base.metadata.create_all(
        engine,
        tables=cast("list[Table]", [DecisionRow.__table__, CritiqueRow.__table__]),
    )


class SqlDecisionJournal(DecisionJournal):
    """Repository translates ORM rows to domain records at the boundary.

    Every provenance field required for later model evaluation is stored as a typed
    column where it is searchable, and as JSON where it is structured.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def save(self, record: DecisionRecord) -> None:
        # Synchronous SQLAlchemy must never run on the event loop: a slow disk
        # or a locked SQLite file would freeze HTTP serving for every client.
        # Retries absorb transient contention; a lost record must not kill the
        # review loop right after its order was filled.
        await with_sqlite_retry("journal.save", self._save_sync, record)

    def _save_sync(self, record: DecisionRecord) -> None:
        with Session(self.engine) as session:
            values = self._row_values(record)
            row = session.get(DecisionRow, record.decision_id)
            if row is None:
                session.add(DecisionRow(**values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            if record.jev_critique and record.jev_provider_metadata:
                session.merge(
                    CritiqueRow(
                        decision_id=record.decision_id,
                        critique=record.jev_critique.model_dump(mode="json"),
                        provider_metadata=record.jev_provider_metadata.model_dump(mode="json"),
                    )
                )
            session.commit()

    async def get(self, decision_id: str) -> DecisionRecord | None:
        return await asyncio.to_thread(self._get_sync, decision_id)

    def _get_sync(self, decision_id: str) -> DecisionRecord | None:
        with Session(self.engine) as session:
            row = session.get(DecisionRow, decision_id)
            return self._to_domain(row) if row else None

    async def list_recent(self, limit: int | None = None) -> list[DecisionRecord]:
        return await asyncio.to_thread(self._list_recent_sync, limit)

    def _list_recent_sync(self, limit: int | None) -> list[DecisionRecord]:
        with Session(self.engine) as session:
            query = select(DecisionRow).order_by(DecisionRow.timestamp.desc())
            if limit is not None:
                query = query.limit(limit)
            rows = session.scalars(query).all()
            return [self._to_domain(row) for row in rows]

    def _row_values(self, record: DecisionRecord) -> dict[str, object]:
        ai_meta = record.initial_provider_metadata
        server = ai_meta.provider_metadata.get("server") if ai_meta.provider_metadata else None
        jev_meta = record.jev_provider_metadata
        return {
            "decision_id": record.decision_id,
            "correlation_id": record.correlation_id,
            "event_id": record.event_id,
            "status": record.status.value,
            "timestamp": record.timestamp,
            "input_context_hash": record.input_context_hash,
            "initial_proposal": record.initial_ai_proposal.model_dump(mode="json"),
            "final_decision": record.final_ai_decision.model_dump(mode="json")
            if record.final_ai_decision
            else None,
            "provider_metadata": ai_meta.model_dump(mode="json"),
            "jev_critique": record.jev_critique.model_dump(mode="json")
            if record.jev_critique
            else None,
            "jev_provider_metadata": jev_meta.model_dump(mode="json") if jev_meta else None,
            "orders_generated": [
                order.model_dump(mode="json") for order in record.orders_generated
            ],
            "portfolio_before": record.portfolio_before.model_dump(mode="json"),
            "portfolio_after": record.portfolio_after.model_dump(mode="json")
            if record.portfolio_after
            else None,
            "symbol": record.symbol,
            "instrument_key": record.instrument_key,
            "market_snapshot_id": record.market_snapshot_id,
            "market_timestamp": record.market_timestamp,
            "ai_server": server if isinstance(server, str) else None,
            "ai_model": ai_meta.model,
            "ai_latency_ms": ai_meta.latency_ms,
            "jev_model": jev_meta.model if jev_meta else None,
            "jev_latency_ms": jev_meta.latency_ms if jev_meta else None,
            "prompt_name": ai_meta.prompt_name,
            "prompt_version": ai_meta.prompt_version,
            "reconsideration_requested": record.reconsideration_requested,
            "reconsideration_performed": record.reconsideration_performed,
            "reconsideration_provider_metadata": record.reconsideration_provider_metadata.model_dump(
                mode="json"
            )
            if record.reconsideration_provider_metadata
            else None,
            "safety_allowed": record.safety_allowed,
            "safety_reason": record.safety_reason,
            "failure_reason": record.failure_reason,
        }

    def _to_domain(self, row: DecisionRow) -> DecisionRecord:
        return DecisionRecord(
            decision_id=row.decision_id,
            correlation_id=row.correlation_id,
            event_id=row.event_id,
            timestamp=row.timestamp,
            status=DecisionStatus(row.status),
            input_context_hash=row.input_context_hash,
            initial_ai_proposal=PortfolioDecision.model_validate(row.initial_proposal),
            initial_provider_metadata=ProviderMetadata.model_validate(row.provider_metadata),
            jev_critique=CriticAssessment.model_validate(row.jev_critique)
            if row.jev_critique
            else None,
            jev_provider_metadata=ProviderMetadata.model_validate(row.jev_provider_metadata)
            if row.jev_provider_metadata
            else None,
            final_ai_decision=FinalAIDecision.model_validate(row.final_decision)
            if row.final_decision
            else None,
            orders_generated=[Order.model_validate(item) for item in (row.orders_generated or [])],
            portfolio_before=PortfolioState.model_validate(row.portfolio_before)
            if row.portfolio_before
            else PortfolioState(cash=Decimal("0")),
            portfolio_after=PortfolioState.model_validate(row.portfolio_after)
            if row.portfolio_after
            else None,
            market_snapshot_id=row.market_snapshot_id,
            market_timestamp=row.market_timestamp,
            instrument_key=row.instrument_key,
            symbol=row.symbol,
            reconsideration_requested=row.reconsideration_requested,
            reconsideration_performed=row.reconsideration_performed,
            reconsideration_provider_metadata=ProviderMetadata.model_validate(
                row.reconsideration_provider_metadata
            )
            if row.reconsideration_provider_metadata
            else None,
            safety_allowed=row.safety_allowed,
            safety_reason=row.safety_reason,
            failure_reason=row.failure_reason,
        )
