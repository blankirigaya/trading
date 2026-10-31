from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ai_trader.domain.decisions import DecisionStatus, PortfolioDecision, ProviderMetadata
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.journal.service import DecisionRecord
from ai_trader.persistence.models import Base
from ai_trader.persistence.repositories import SqlDecisionJournal


async def test_sql_decision_journal_returns_domain_record(clock) -> None:
    # The journal runs SQLite work in worker threads, and a plain ``sqlite://``
    # engine gives every thread its own empty in-memory database. One shared
    # connection keeps the test's table visible to the journal, as a file-backed
    # database is in production.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    journal = SqlDecisionJournal(engine)
    record = DecisionRecord(
        decision_id="d",
        correlation_id="c",
        event_id="e",
        timestamp=clock.now(),
        status=DecisionStatus.CREATED,
        input_context_hash="hash",
        initial_ai_proposal=PortfolioDecision(
            cash_percent=100, allocations=[], proposals=[], market_summary="m", decision_summary="s"
        ),
        initial_provider_metadata=ProviderMetadata(
            provider="mock", model="m", prompt_name="p", prompt_version="v1"
        ),
        portfolio_before=PortfolioState(cash=Decimal("100")),
    )
    await journal.save(record)
    loaded = await journal.get("d")
    assert loaded is not None
    assert loaded.decision_id == "d"
    assert loaded.initial_ai_proposal.market_summary == "m"
