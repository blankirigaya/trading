"""Jev entry gate: weak entries are denied, exits always pass.

A critic score below the floor must stop OPEN/INCREASE proposals even when the
critic agrees — those weak agrees used to sail through and bleed on charges.
CLOSE/REDUCE proposals are never gated: banking a winner stays frictionless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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
from ai_trader.domain.portfolio import Position
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _meta() -> ProviderMetadata:
    return ProviderMetadata(
        provider="test",
        model="test-model",
        prompt_name="portfolio_manager",
        prompt_version="v12",
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


def _open(symbol: str) -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=0.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="p-1",
                symbol=symbol,
                action=PortfolioAction.OPEN_POSITION,
                side=OrderSide.BUY,
                capital_allocation_inr=Decimal("25000"),
                quantity_shares=10,
                confidence=0.7,
                thesis="Board pick.",
                evidence=["board screen"],
                conditions_that_change_thesis=["reversal"],
            )
        ],
        market_summary="board",
        decision_summary="buy pick",
    )


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


class ScriptedAI(AIProvider):
    def __init__(self, decisions: list[PortfolioDecision]) -> None:
        self.decisions = decisions
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "scripted"

    async def structured_completion(
        self, system_prompt: Any, user_prompt: Any, response_model: Any, **kwargs: Any
    ) -> Any:
        self.calls += 1
        payload = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        return payload, _meta()


class ScoredCritic(JevCritic):
    def __init__(self, score: float, agrees: bool = True) -> None:
        self._score = score
        self._agrees = agrees

    async def assess(self, proposal: Any, news: Any = None) -> Any:
        return (
            CriticAssessment(
                score=self._score,
                agrees=self._agrees,
                disagreement_signals=[],
                critique="fine" if self._agrees else "weak",
                bounded_decisions=["APPROVE"] if self._agrees else ["CHALLENGE"],
            ),
            _meta(),
        )


def _workflow(clock: Any, ai: ScriptedAI, critic: ScoredCritic, broker: PaperBroker) -> ShadowReviewWorkflow:
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v13"),
        critic,
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        InMemoryDecisionJournal(),
        clock,
        jev_min_entry_score=0.65,
    )


def _hold(broker: PaperBroker, symbol: str, quantity: int, average: str) -> None:
    broker.positions[symbol] = Position(
        symbol=symbol, quantity=quantity, average_entry_price=Decimal(average)
    )


def test_weak_agreed_entry_is_denied_not_executed(clock: Any) -> None:
    import asyncio

    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    workflow = _workflow(clock, ScriptedAI([_open("INFY")]), ScoredCritic(0.4, agrees=True), broker)
    record = asyncio.run(workflow.run(infy, board=[infy]))
    assert record.status.value == "DENIED_BY_SAFETY"
    assert record.orders_generated == []
    assert "jev gate" in (record.failure_reason or "")
    assert broker.realized_pnl == Decimal("0")


def test_strong_entry_passes_the_gate(clock: Any) -> None:
    import asyncio

    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    workflow = _workflow(clock, ScriptedAI([_open("INFY")]), ScoredCritic(0.9, agrees=True), broker)
    record = asyncio.run(workflow.run(infy, board=[infy]))
    assert record.status.value == "EXECUTED"
    assert len(record.orders_generated) == 1


def test_exit_is_never_gated_even_at_zero_score(clock: Any) -> None:
    import asyncio

    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    _hold(broker, "LT", 6, "100")
    broker.set_price("LT", Decimal("110"))
    workflow = _workflow(clock, ScriptedAI([_close("LT", 6)]), ScoredCritic(0.0, agrees=False), broker)
    record = asyncio.run(workflow.run(infy, board=[infy], prices={"LT": Decimal("110")}))
    assert record.status.value == "EXECUTED"
    assert record.orders_generated[0].request.symbol == "LT"


def test_gate_threshold_is_configurable(clock: Any) -> None:
    import asyncio

    infy = _state("INFY", "1500.25")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("1500.25")})
    workflow = _workflow(clock, ScriptedAI([_open("INFY")]), ScoredCritic(0.8, agrees=True), broker)
    workflow.jev_min_entry_score = 0.9
    record = asyncio.run(workflow.run(infy, board=[infy]))
    assert record.status.value == "DENIED_BY_SAFETY"
    assert record.orders_generated == []
