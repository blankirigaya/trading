"""Board-aware reviews: the AI picks its own symbol from the live board."""

from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import (
    CriticAssessment,
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.orders import OrderSide
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _meta() -> ProviderMetadata:
    return ProviderMetadata(
        provider="test",
        model="test-model",
        prompt_name="portfolio_manager",
        prompt_version="v5",
    )


def _state(symbol: str, price: str) -> MarketState:
    now = datetime.now(UTC)
    tick = MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal(price),
        exchange_timestamp=now,
        received_timestamp=now,
        processed_timestamp=now,
        raw_payload={},
    )
    candle = Candle(
        start_timestamp=now,
        end_timestamp=now,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=now,
        last_price=Decimal(price),
        previous_close=Decimal(price),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


def _decision(symbol: str, allocation: str, shares: int) -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=0.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="p-1",
                symbol=symbol,
                action=PortfolioAction.OPEN_POSITION,
                side=OrderSide.BUY,
                capital_allocation_inr=Decimal(allocation),
                quantity_shares=shares,
                confidence=0.7,
                thesis="Board pick.",
                evidence=["board screen"],
                conditions_that_change_thesis=["reversal"],
            )
        ],
        market_summary="board",
        decision_summary="buy pick",
    )


class ScriptedAI(AIProvider):
    def __init__(self, decisions: list[PortfolioDecision]) -> None:
        self.decisions = decisions
        self.calls = 0
        self.contexts: list[str] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.calls += 1
        self.contexts.append(str(user_prompt))
        payload = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        return payload, _meta()


class ApprovingCritic(JevCritic):
    async def assess(self, proposal, news=None):
        return (
            CriticAssessment(
                score=0.9,
                agrees=True,
                disagreement_signals=[],
                critique="fine",
                bounded_decisions=["APPROVE"],
            ),
            _meta(),
        )


def _workflow(clock, ai, broker) -> ShadowReviewWorkflow:
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v6"),
        ApprovingCritic(),
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        InMemoryDecisionJournal(),
        clock,
    )


async def _account(broker):
    return await broker.get_account()


def test_ai_pick_is_priced_on_picked_symbol(clock, tmp_path) -> None:
    from sqlalchemy import create_engine

    from ai_trader.persistence.models import Base
    from ai_trader.persistence.repositories import SqlDecisionJournal

    engine = create_engine(f"sqlite:///{tmp_path / 'board.db'}")
    Base.metadata.create_all(engine)
    journal = SqlDecisionJournal(engine)

    infy = _state("INFY", "1500.25")
    tcs = _state("TCS", "2500.00")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25"), "TCS": Decimal("2500.00")})
    workflow = ShadowReviewWorkflow(
        PortfolioManagerAgent(ScriptedAI([_decision("TCS", "25000", 10)]), "v6"),
        ApprovingCritic(),
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        journal,
        clock,
    )
    import asyncio

    record = asyncio.run(workflow.run(infy, board=[infy, tcs]))

    assert record.status.value == "EXECUTED"
    assert record.symbol == "TCS"
    order = record.orders_generated[0]
    assert order.request.symbol == "TCS"
    # 25000 / 2500 = 10 shares at the TCS price, not the INFY price.
    assert order.request.quantity == 10
    assert order.request.actual_order_value == Decimal("25000")
    # The board context reached the AI.
    assert "TCS" in workflow.manager.provider.contexts[0]
    assert "INFY" in workflow.manager.provider.contexts[0]


def test_unknown_symbol_proposal_holds_instead_of_trading_blind(clock) -> None:
    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    workflow = _workflow(clock, ScriptedAI([_decision("FAKE", "10000", 5)]), broker)
    import asyncio

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.orders_generated == []
    assert record.status.value != "EXECUTED"


def test_hold_action_with_populated_fields_does_not_trade(clock) -> None:
    from ai_trader.domain.decisions import PortfolioAction

    infy = _state("INFY", "1500.25")
    hold_like_trade = _decision("INFY", "15000", 10).model_copy(
        update={
            "proposals": [
                _decision("INFY", "15000", 10).proposals[0].model_copy(
                    update={"action": PortfolioAction.HOLD}
                )
            ]
        }
    )
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    workflow = _workflow(clock, ScriptedAI([hold_like_trade]), broker)
    import asyncio

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.orders_generated == []
    assert record.status.value != "EXECUTED"


def _close(symbol: str, shares: int) -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=100.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="close-1",
                symbol=symbol,
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                capital_allocation_inr=None,
                quantity_shares=shares,
                confidence=0.8,
                thesis="Exit: momentum broke.",
                evidence=["price below entry"],
                conditions_that_change_thesis=["reclaim of the open"],
            )
        ],
        market_summary="board",
        decision_summary="exit",
    )


def _hold(broker, symbol: str, quantity: int, average: str) -> None:
    from ai_trader.domain.portfolio import Position

    broker.positions[symbol] = Position(
        symbol=symbol, quantity=quantity, average_entry_price=Decimal(average)
    )


def test_held_position_closes_when_its_symbol_is_off_the_board(clock) -> None:
    """A stale held symbol must not strand the exit: close it at the live mark."""
    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    _hold(broker, "LT", 6, "100")
    broker.set_price("LT", Decimal("110"))
    workflow = _workflow(clock, ScriptedAI([_close("LT", 6)]), broker)
    import asyncio

    record = asyncio.run(workflow.run(infy, board=[infy], prices={"LT": Decimal("110")}))

    assert record.status.value == "EXECUTED"
    # The decision is journalled against the symbol that was actually sold.
    assert record.symbol == "LT"
    assert record.market_snapshot_id is not None
    assert record.market_snapshot_id.endswith(":mark:LT")
    order = record.orders_generated[0]
    assert order.request.symbol == "LT"
    assert order.request.quantity == 6
    # Priced from the live mark, not from the reviewed INFY snapshot.
    assert order.request.actual_order_value == Decimal("660")
    assert broker.realized_pnl == Decimal("60")


def test_off_board_exit_without_a_mark_price_is_refused(clock) -> None:
    """No live price means no exit: refuse loudly instead of filling blind."""
    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    _hold(broker, "LT", 6, "100")
    workflow = _workflow(clock, ScriptedAI([_close("LT", 6)]), broker)
    import asyncio

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.orders_generated == []
    assert record.status.value == "APPROVED_BY_AI"
    assert broker.positions["LT"].quantity == 6
