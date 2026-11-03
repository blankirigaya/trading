"""FYERS prices driving an AI-judged paper portfolio.

Transport boundaries are simulated. This proves behavior, not a real run.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.fyers_session import build_session
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
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.execution.service import ExecutionService
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.fyers.client import FyersDataSocket
from ai_trader.integrations.fyers.feed import FyersFeed
from ai_trader.integrations.fyers.market_data import FyersMarketDataService
from ai_trader.persistence.models import Base
from ai_trader.persistence.repositories import SqlDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

FIXTURES = Path(__file__).parent.parent / "fixtures"
TOPIC = "sf|nse_cm|3045"
PRICE_UNIT = Decimal("1500.25")  # exact whole-share multiple of the fixture price


def _settings(tmp_path) -> Settings:
    return Settings(
        app_mode="LIVE_SHADOW",
        market_provider="fyers",
        shadow_initial_capital_inr=Decimal("100000"),
        trading_enabled=True,
        max_ai_capital_inr=Decimal("50000"),
        max_single_position_inr=Decimal("50000"),
        database_url=f"sqlite:///{tmp_path / 'shadow.db'}",
        ai_base_url="http://192.168.1.25:8080/v1",
        ai_model="server-model-id",
        jev_enabled=True,
        jev_api_key="jev-secret",
    )


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'journal.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _decision(
    action: PortfolioAction = PortfolioAction.OPEN_POSITION,
    side: OrderSide | None = OrderSide.BUY,
    allocation: str | None = str(PRICE_UNIT * 10),
    cash_percent: float = 85.0,
) -> PortfolioDecision:
    proposals = []
    if action in (
        PortfolioAction.OPEN_POSITION,
        PortfolioAction.INCREASE_POSITION,
        PortfolioAction.REDUCE_POSITION,
        PortfolioAction.CLOSE_POSITION,
    ):
        proposals.append(
            TradeProposal(
                proposal_id="p-1",
                symbol="INFY",
                action=action,
                side=side,
                capital_allocation_inr=Decimal(allocation) if allocation else None,
                confidence=0.73,
                thesis="Momentum with volume confirmation.",
                evidence=["FYERS LTP 1500.25"],
                conditions_that_change_thesis=["index gap down"],
            )
        )
    return PortfolioDecision(
        cash_percent=cash_percent,
        allocations=[],
        proposals=proposals,
        market_summary="INFY holding near session high on real FYERS volume.",
        decision_summary="Open a measured long position." if proposals else "Stay in cash.",
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
        payload = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        return payload, ProviderMetadata(
            provider="remote_openai_compatible",
            model="server-model-id",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=900,
            provider_metadata={"server": "192.168.1.25:8080"},
        )


class ScriptedCritic(JevCritic):
    def __init__(self, agrees: bool = True) -> None:
        self.agrees = agrees

    async def assess(self, proposal, news=None):
        return (
            CriticAssessment(
                score=0.85 if self.agrees else 0.35,
                agrees=self.agrees,
                disagreement_signals=[] if self.agrees else ["evidence thin"],
                critique="ok",
                bounded_decisions=["APPROVE"] if self.agrees else ["REQUEST_RECONSIDERATION"],
            ),
            ProviderMetadata(
                provider="typesafe-jev",
                model="jev-latest",
                prompt_name="critic",
                prompt_version="v1",
                latency_ms=300,
            ),
        )


class BrokenAI(AIProvider):
    @property
    def model_name(self) -> str:
        return "unreachable"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        from ai_trader.ai.remote_model import RemoteAIUnavailable

        raise RemoteAIUnavailable("AI structured completion failed")


class BrokenCritic(JevCritic):
    async def assess(self, proposal, news=None):
        from ai_trader.ai.jev import JevUnavailable

        raise JevUnavailable("Jev returned malformed critique")


def _feed(clock):
    instrument_map = {"sf|nse_cm|3045": None}
    from ai_trader.integrations.fyers.instruments import to_instrument

    instrument = to_instrument("NSE:INFY-EQ", 3045)
    service = FyersMarketDataService({TOPIC: instrument}, clock)
    socket = FyersDataSocket(FyersAuth("app", "token"), on_frame=lambda *_: None)
    feed = FyersFeed(service, socket, {TOPIC: instrument}, [TOPIC], {TOPIC: "NSE:INFY-EQ"})
    return feed, instrument, instrument_map


def _workflow(clock, ai, critic, journal):
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": PRICE_UNIT})
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v4"),
        critic,
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        journal,
        clock,
    )


async def test_buy_uses_real_price_and_produces_paper_order(clock, db_engine) -> None:
    feed, instrument, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    assert state is not None

    journal = SqlDecisionJournal(db_engine)
    record = await _workflow(clock, ScriptedAI([_decision()]), ScriptedCritic(), journal).run(state)

    assert record.status is DecisionStatus.EXECUTED
    order = record.orders_generated[0]
    assert order.request.quantity == 10
    assert order.request.actual_order_value == PRICE_UNIT * 10
    assert order.request.symbol == "INFY"


async def test_hold_produces_no_order(clock, db_engine) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)

    hold = _decision(
        action=PortfolioAction.STAY_IN_CASH, side=None, allocation=None, cash_percent=100.0
    )
    record = await _workflow(clock, ScriptedAI([hold]), ScriptedCritic(), journal).run(state)

    assert record.status is DecisionStatus.APPROVED_BY_AI
    assert record.orders_generated == []


async def test_sell_without_position_is_journalled_not_crashed(clock, db_engine) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)
    ai = ScriptedAI([_decision(side=OrderSide.SELL, allocation=str(PRICE_UNIT * 10))])
    record = await _workflow(clock, ai, ScriptedCritic(), journal).run(state)

    assert record.status is DecisionStatus.FAILED
    assert "paper_broker" in (record.failure_reason or "")
    assert record.orders_generated == []
    stored = await journal.get(record.decision_id)
    assert stored is not None and stored.failure_reason


async def test_sell_closes_an_existing_paper_position(clock, db_engine) -> None:
    """A SELL against a held position fills through PaperBroker at the real FYERS price."""
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)
    ai = ScriptedAI(
        [
            _decision(side=OrderSide.BUY, allocation=str(PRICE_UNIT * 10)),
            _decision(
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                allocation=str(PRICE_UNIT * 10),
            ),
        ]
    )
    workflow = _workflow(clock, ai, ScriptedCritic(), journal)

    first = await workflow.run(state)
    assert first.status is DecisionStatus.EXECUTED
    assert workflow.broker.positions["INFY"].quantity == 10

    second = await workflow.run(state.model_copy(update={"snapshot_id": "snapshot-2"}))
    assert second.status is DecisionStatus.EXECUTED
    assert second.orders_generated[0].request.side == OrderSide.SELL
    # Position fully closed; no FYERS order was involved.
    assert workflow.broker.positions["INFY"].quantity == 0
    assert all(
        order.broker_order_id.startswith("PAPER-") for order in await workflow.broker.get_orders()
    )


async def test_remote_ai_failure_yields_no_trade(clock, db_engine) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)

    record = await _workflow(clock, BrokenAI(), ScriptedCritic(), journal).run(state)
    assert record.status is DecisionStatus.FAILED
    # Named for the provider fault, not conflated with a deliberate hold.
    assert "ai:" in (record.failure_reason or "")
    assert record.orders_generated == []


async def test_jev_failure_yields_no_new_trade(clock, db_engine) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)

    record = await _workflow(clock, ScriptedAI([_decision()]), BrokenCritic(), journal).run(state)
    assert record.status is DecisionStatus.FAILED
    assert "jev" in (record.failure_reason or "")
    assert record.orders_generated == []


async def test_duplicate_decision_does_not_duplicate_paper_order(clock, db_engine) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    state = feed.latest()
    journal = SqlDecisionJournal(db_engine)

    workflow = _workflow(clock, ScriptedAI([_decision()]), ScriptedCritic(), journal)
    first = await workflow.run(state)
    second = await workflow.run(state)

    assert first.decision_id == second.decision_id
    assert len(await workflow.broker.get_orders()) == 1
    assert workflow.broker.positions["INFY"].quantity == 10


async def test_paper_position_marks_to_new_real_price(clock) -> None:
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    request = OrderRequest(
        client_order_id="c",
        decision_id="d",
        correlation_id="corr",
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=PRICE_UNIT * 10,
        actual_order_value=PRICE_UNIT * 10,
        conversion_reason="test",
    )
    await broker.place_order(request)
    entry = Decimal("1500.25")
    assert broker.cash == Decimal("100000") - entry * 10

    # Real FYERS price rises to 1515.25.
    broker.mark_to_market({"INFY": Decimal("1515.25")})
    assert broker.unrealized_pnl() == (Decimal("1515.25") - entry) * 10
    assert broker.unrealized_pnl() == Decimal("150")


async def test_paper_pnl_accounting_after_partial_sell(clock) -> None:
    entry = Decimal("1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": entry})

    def make(client_id: str, side: OrderSide, quantity: int) -> OrderRequest:
        return OrderRequest(
            client_order_id=client_id,
            decision_id=client_id,
            correlation_id="c",
            symbol="INFY",
            exchange="NSE",
            segment="EQ",
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            requested_allocation=entry * quantity,
            actual_order_value=entry * quantity,
            conversion_reason="test",
        )

    await broker.place_order(make("buy", OrderSide.BUY, 10))
    broker.mark_to_market({"INFY": Decimal("1510.25")})
    await broker.place_order(make("sell", OrderSide.SELL, 4))

    position = broker.positions["INFY"]
    assert position.quantity == 6
    assert position.average_entry_price == entry
    # Realized on 4 shares at +10.00 each, against the real FYERS mark.
    assert position.realized_pnl == Decimal("40")
    broker.mark_to_market({"INFY": Decimal("1500.25")})
    assert broker.unrealized_pnl() == Decimal("0")


def test_initial_capital_is_configurable(tmp_path, clock) -> None:
    feed, _, _ = _feed(clock)
    feed.service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    session = build_session(
        Settings(
            app_mode="LIVE_SHADOW",
            shadow_initial_capital_inr=Decimal("250000"),
            ai_review_shards=1,
            database_url=f"sqlite:///{tmp_path / 's.db'}",
            ai_base_url="http://ai:8080/v1",
            ai_model="m",
            jev_api_key="k",
        ),
        feed,
        clock=clock,
    )
    assert len(session.shards) == 1
    assert session.shards[0].broker.cash == Decimal("250000")
    assert session.shards[0].broker.set_price("INFY", Decimal("1500.25")) is None


