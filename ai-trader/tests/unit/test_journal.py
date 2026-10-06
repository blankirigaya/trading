from decimal import Decimal

from sqlalchemy import create_engine

from ai_trader.domain.decisions import DecisionStatus, PortfolioDecision, ProviderMetadata
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.journal.service import DecisionRecord
from ai_trader.persistence.models import Base
from ai_trader.persistence.repositories import SqlDecisionJournal


async def test_sql_decision_journal_returns_domain_record(clock) -> None:
    engine = create_engine("sqlite://")
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
