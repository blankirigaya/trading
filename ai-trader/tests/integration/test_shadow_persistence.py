"""SQL persistence of the real acceptance decision, with mocked transport boundaries only.

These tests verify journalling and translation. They are not the real acceptance run.
"""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import (
    CriticAssessment,
    DecisionStatus,
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Instrument, MarketState, MarketTick
from ai_trader.domain.orders import OrderSide
from ai_trader.execution.service import ExecutionService
from ai_trader.persistence.models import Base
from ai_trader.persistence.repositories import SqlDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _trade() -> TradeProposal:
    # Exactly 10 whole shares at 1500.25 so integer conversion is lossless.
    return TradeProposal(
        proposal_id="p-1",
        symbol="INFY",
        action=PortfolioAction.OPEN_POSITION,
        side=OrderSide.BUY,
        capital_allocation_inr=Decimal("15002.50"),
        confidence=0.71,
        thesis="Momentum with volume confirmation after a clean base.",
        evidence=["live V3 LTP 1500.25", "bid/ask spread 0.10"],
        conditions_that_change_thesis=["index gap down", "spread widens"],
    )


def _decision(allocation: str = "15002.50") -> PortfolioDecision:
    proposal = _trade().model_copy(update={"capital_allocation_inr": Decimal(allocation)})
    return PortfolioDecision(
        cash_percent=85.0,
        allocations=[],
        proposals=[proposal],
        market_summary="INFY trading near session high on rising volume.",
        decision_summary="Open a measured long position.",
    )


def _state(clock) -> MarketState:
    tick = MarketTick(
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("1500.25"),
        exchange_timestamp=clock.now(),
        received_timestamp=clock.now(),
        processed_timestamp=clock.now(),
        raw_payload={"feeds": {"NSE_EQ|INE009A01021": {"ltp": 1500.25}}},
    )
    return MarketState(
        snapshot_id="snapshot-1",
        instrument=Instrument(instrument_key="NSE_EQ|INE009A01021", symbol="INFY", exchange="NSE"),
        timestamp=clock.now(),
        last_price=Decimal("1500.25"),
        previous_close=Decimal("1490.10"),
        recent_ticks=[tick],
    )


class ScriptedAI(AIProvider):
    def __init__(self, decisions: list[PortfolioDecision]) -> None:
        self.decisions = decisions
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "scripted-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.calls += 1
        decision = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        return decision, ProviderMetadata(
            provider="remote_openai_compatible",
            model="scripted-model",
            request_id="req-scripted",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=42,
            input_tokens=100,
            output_tokens=50,
            provider_metadata={"server": "192.168.1.25:8080", "schema_validation": "passed"},
        )


class ScriptedCritic(JevCritic):
    def __init__(self, assessment: CriticAssessment) -> None:
        self.assessment = assessment

    async def assess(self, proposal, news=None):
        return self.assessment, ProviderMetadata(
            provider="typesafe-jev",
            model="jev-latest",
            prompt_name="critic",
            prompt_version="v1",
            latency_ms=77,
            provider_metadata={"server": "api.typesafe.ai"},
        )


def _workflow(clock, ai: AIProvider, critic: JevCritic, journal):
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v2"),
        critic,
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        journal,
        clock,
    )


@pytest.fixture
def sql_journal():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield SqlDecisionJournal(engine)
    engine.dispose()


async def test_jev_approval_persists_all_stages_and_provenance(clock, sql_journal) -> None:
    ai = ScriptedAI([_decision()])
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.82,
            agrees=True,
            disagreement_signals=[],
            critique="Well evidenced.",
            bounded_decisions=["APPROVE"],
        )
    )
    record = await _workflow(clock, ai, critic, sql_journal).run(_state(clock))

    assert record.status is DecisionStatus.EXECUTED
    assert record.reconsideration_requested is False
    assert record.reconsideration_performed is False
    assert record.orders_generated
    assert record.portfolio_after is not None

    stored = await sql_journal.get(record.decision_id)
    assert stored is not None
    assert stored.initial_ai_proposal.proposals[0].thesis
    assert stored.jev_critique is not None
    assert stored.final_ai_decision is not None
    assert stored.market_snapshot_id == "snapshot-1"
    assert stored.symbol == "INFY"
    assert stored.instrument_key == "NSE_EQ|INE009A01021"
    assert stored.market_timestamp is not None
    assert stored.safety_allowed is True
    assert stored.orders_generated[0].broker_order_id
    assert stored.portfolio_before.cash == Decimal("100000")
    assert stored.portfolio_after.cash < Decimal("100000")


async def test_jev_challenge_triggers_reconsideration_and_keeps_both(clock, sql_journal) -> None:
    ai = ScriptedAI([_decision(), _decision("6001.00")])
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.4,
            agrees=False,
            disagreement_signals=["allocation too large"],
            critique="Halve it.",
            bounded_decisions=["REQUEST_RECONSIDERATION"],
        )
    )
    record = await _workflow(clock, ai, critic, sql_journal).run(_state(clock))

    assert record.reconsideration_requested is True
    assert record.reconsideration_performed is True
    # The reconsidered decision went on to execute, so EXECUTED is the terminal status;
    # the RECONSIDERED stage is preserved explicitly via the two provenance flags.
    assert record.status is DecisionStatus.EXECUTED
    assert ai.calls == 2

    # The original proposal must survive unchanged alongside the reconsidered decision.
    assert record.initial_ai_proposal.proposals[0].capital_allocation_inr == Decimal("15002.50")
    assert record.final_ai_decision is not None
    assert record.final_ai_decision.proposals[0].capital_allocation_inr == Decimal("6001.00")

    stored = await sql_journal.get(record.decision_id)
    assert stored is not None
    assert stored.initial_ai_proposal.proposals[0].capital_allocation_inr == Decimal("15002.50")
    assert stored.final_ai_decision.proposals[0].capital_allocation_inr == Decimal("6001.00")
    assert stored.reconsideration_requested is True
    assert stored.reconsideration_performed is True
    # The smaller reconsidered allocation is what actually reached the paper broker.
    assert stored.orders_generated[0].request.quantity == 4
    assert stored.orders_generated[0].request.actual_order_value == Decimal("6001.00")


async def test_hold_decision_is_a_success_with_no_order(clock, sql_journal) -> None:
    hold = PortfolioDecision(
        cash_percent=100.0,
        allocations=[],
        proposals=[],
        market_summary="No edge at current levels.",
        decision_summary="Stay in cash and wait for a better entry.",
    )
    ai = ScriptedAI([hold])
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.9,
            agrees=True,
            disagreement_signals=[],
            critique="Correct.",
            bounded_decisions=["APPROVE"],
        )
    )
    record = await _workflow(clock, ai, critic, sql_journal).run(_state(clock))

    assert record.status is DecisionStatus.APPROVED_BY_AI
    assert record.orders_generated == []
    assert (
        record.portfolio_before.cash == record.portfolio_after.cash
        if record.portfolio_after
        else True
    )
    stored = await sql_journal.get(record.decision_id)
    assert stored is not None
    assert stored.orders_generated == []


async def test_jev_failure_journals_failure_without_trade(clock, sql_journal) -> None:
    from ai_trader.ai.jev import JevUnavailable

    class BrokenCritic(JevCritic):
        async def assess(self, proposal, news=None):
            raise JevUnavailable("Jev returned malformed critique")

    record = await _workflow(clock, ScriptedAI([_decision()]), BrokenCritic(), sql_journal).run(
        _state(clock)
    )
    assert record.status is DecisionStatus.FAILED
    assert "jev" in (record.failure_reason or "")
    assert record.orders_generated == []
    stored = await sql_journal.get(record.decision_id)
    assert stored is not None and stored.failure_reason


async def test_ai_failure_journals_failure_without_trade(clock, sql_journal) -> None:
    from ai_trader.ai.remote_model import RemoteAIUnavailable

    class BrokenAI(AIProvider):
        @property
        def model_name(self) -> str:
            return "down"

        async def structured_completion(
            self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
        ):
            raise RemoteAIUnavailable("AI structured completion failed")

    record = await _workflow(
        clock,
        BrokenAI(),
        ScriptedCritic(
            CriticAssessment(
                score=0.5,
                agrees=True,
                disagreement_signals=[],
                critique="n/a",
                bounded_decisions=["APPROVE"],
            )
        ),
        sql_journal,
    ).run(_state(clock))
    assert record.status is DecisionStatus.FAILED
    assert "local_ai" in (record.failure_reason or "")
    assert record.orders_generated == []


async def test_safety_denial_is_recorded_with_reason(clock, sql_journal) -> None:
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    disabled = Settings(trading_enabled=False)
    workflow = ShadowReviewWorkflow(
        PortfolioManagerAgent(ScriptedAI([_decision("99999999")]), "v2"),
        ScriptedCritic(
            CriticAssessment(
                score=0.7,
                agrees=True,
                disagreement_signals=[],
                critique="ok",
                bounded_decisions=["APPROVE"],
            )
        ),
        broker,
        SafetyEnvelope(disabled, KillSwitch(), clock),
        ExecutionService(broker),
        sql_journal,
        clock,
    )
    record = await workflow.run(_state(clock))
    assert record.safety_allowed is False
    assert record.safety_reason == "Trading disabled"
    assert record.orders_generated == []


async def test_duplicate_review_does_not_duplicate_execution(clock, sql_journal) -> None:
    ai = ScriptedAI([_decision()])
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.8,
            agrees=True,
            disagreement_signals=[],
            critique="ok",
            bounded_decisions=["APPROVE"],
        )
    )
    workflow = _workflow(clock, ai, critic, sql_journal)
    state = _state(clock)
    first = await workflow.run(state)
    second = await workflow.run(state)
    assert first.decision_id == second.decision_id
    assert len(await workflow.broker.get_orders()) == 1


async def test_unrepresentable_allocation_is_refused_not_silently_resized(
    clock, sql_journal
) -> None:
    """15000 INR cannot buy whole shares of a 1500.25 INR stock within tolerance."""
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.8,
            agrees=True,
            disagreement_signals=[],
            critique="ok",
            bounded_decisions=["APPROVE"],
        )
    )
    record = await _workflow(clock, ScriptedAI([_decision("15000")]), critic, sql_journal).run(
        _state(clock)
    )

    assert record.status is DecisionStatus.FAILED
    assert "materially change requested allocation" in (record.failure_reason or "")
    assert record.orders_generated == []


async def test_journal_never_stores_credentials(clock, sql_journal) -> None:
    ai = ScriptedAI([_decision()])
    ai.decisions = [_decision()]
    critic = ScriptedCritic(
        CriticAssessment(
            score=0.8,
            agrees=True,
            disagreement_signals=[],
            critique="ok",
            bounded_decisions=["APPROVE"],
        )
    )
    record = await _workflow(clock, ai, critic, sql_journal).run(_state(clock))
    serialized = record.model_dump_json()
    for secret in ("hunter2", "Authorization", "Bearer", "api_key"):
        assert secret not in serialized
