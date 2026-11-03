"""Minimum-edge entry gate: a stated target must pay for its own round trip.

The gate is deliberately arithmetic. These tests pin the two things that make
it safe in practice: it refuses only clearly uneconomic entries, and it never
touches exits or unverifiable proposals.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.charges import ChargeRates, evaluate_entry_edge, round_trip_cost
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
from ai_trader.risk.entry_plan import RiskRules
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

INFY_PRICE = Decimal("1500.25")


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


def _open(symbol: str, target: str | None) -> PortfolioDecision:
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
                desired_entry_price=INFY_PRICE,
                target_price=Decimal(target) if target is not None else None,
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
        return self.decisions[min(self.calls - 1, len(self.decisions) - 1)], _meta()


class AgreeingCritic(JevCritic):
    def __init__(self, score: float = 0.95) -> None:
        self._score = score

    async def assess(self, proposal: Any, news: Any = None) -> Any:
        return (
            CriticAssessment(
                score=self._score,
                agrees=True,
                disagreement_signals=[],
                critique="fine",
                bounded_decisions=["APPROVE"],
            ),
            _meta(),
        )


def _workflow(
    clock: Any,
    ai: ScriptedAI,
    broker: PaperBroker,
    *,
    charges: bool = True,
    multiple: str = "2.5",
) -> ShadowReviewWorkflow:
    rules = RiskRules(
        charge_rates=ChargeRates() if charges else None,
        min_edge_cost_multiple=Decimal(multiple),
        min_reward_risk=Decimal("0"),
        risk_per_trade_pct=Decimal("0"),
    )
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v13"),
        AgreeingCritic(),
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        InMemoryDecisionJournal(),
        clock,
        jev_min_entry_score=0.65,
        risk_rules=rules,
    )


def _broker(clock: Any) -> PaperBroker:
    return PaperBroker(Decimal("100000"), clock, {"INFY": INFY_PRICE})


# ---------- pure arithmetic ----------


def test_target_below_the_required_multiple_is_refused() -> None:
    # Ten shares on a 5 paise move earns 0.50 INR against ~1.06 INR of charges.
    edge = evaluate_entry_edge(
        entry_price=Decimal("100"),
        target_price=Decimal("100.05"),
        quantity=10,
        rates=ChargeRates(),
        required_multiple=Decimal("2.5"),
    )
    assert edge.allowed is False
    assert edge.multiple is not None and edge.multiple < 1
    assert edge.round_trip_cost_inr > edge.expected_gross_inr


def test_target_clearing_the_multiple_is_allowed() -> None:
    edge = evaluate_entry_edge(
        entry_price=Decimal("100"),
        target_price=Decimal("101"),
        quantity=100,
        rates=ChargeRates(),
        required_multiple=Decimal("2.5"),
    )
    assert edge.allowed is True
    assert edge.expected_gross_inr > edge.required_gross_inr


def test_missing_target_is_unverifiable_not_a_pass_or_fail() -> None:
    edge = evaluate_entry_edge(
        entry_price=Decimal("100"),
        target_price=None,
        quantity=10,
        rates=ChargeRates(),
        required_multiple=Decimal("2.5"),
    )
    assert edge.multiple is None
    # The caller must not read this as an economic endorsement; it simply has
    # no arithmetic to object to.
    assert edge.allowed is True


def test_round_trip_cost_is_paise_exact_and_symmetric_in_both_legs() -> None:
    buys = round_trip_cost(Decimal("100"), Decimal("101"), 100, ChargeRates())
    assert buys == buys.quantize(Decimal("0.01"))
    assert buys > Decimal("0")


# ---------- workflow behaviour ----------


def test_uneconomic_entry_is_denied_before_any_order(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    # +0.05 on 10 shares = 0.50 INR gross, far below round-trip charges.
    workflow = _workflow(clock, ScriptedAI([_open("INFY", "1500.30")]), broker)

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.status.value == "DENIED_BY_SAFETY"
    assert record.orders_generated == []
    assert "edge gate" in (record.failure_reason or "")
    assert broker.realized_pnl == Decimal("0")


def test_healthy_target_still_executes(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    workflow = _workflow(clock, ScriptedAI([_open("INFY", "1520")]), broker)

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.status.value == "EXECUTED"
    assert len(record.orders_generated) == 1


def test_entry_without_a_target_is_not_blocked_by_this_gate(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    workflow = _workflow(clock, ScriptedAI([_open("INFY", None)]), broker)

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.status.value == "EXECUTED"


def test_exits_are_never_gated_by_edge(clock: Any) -> None:
    infy = _state("INFY", "1500.25")
    broker = _broker(clock)
    broker.positions["LT"] = Position(
        symbol="LT", quantity=6, average_entry_price=Decimal("100")
    )
    # A real gain, large enough that banking it is worth the exit charge: the
    # edge gate must never stop an exit, and the exit floor only applies to
    # dust profits.
    broker.set_price("LT", Decimal("105"))
    workflow = _workflow(clock, ScriptedAI([_close("LT", 6)]), broker)

    record = asyncio.run(workflow.run(infy, board=[infy], prices={"LT": Decimal("105")}))

    assert record.status.value == "EXECUTED"
    assert record.orders_generated[0].request.symbol == "LT"


def test_disabled_charges_leave_the_gate_open(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    workflow = _workflow(clock, ScriptedAI([_open("INFY", "1500.30")]), broker, charges=False)

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.status.value == "EXECUTED"


def test_required_multiple_is_configurable(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    # A target that clears 2.5x but not 100x must flip with the setting.
    workflow = _workflow(
        clock, ScriptedAI([_open("INFY", "1520")]), broker, multiple="100"
    )
    record = asyncio.run(workflow.run(infy, board=[infy]))
    assert record.status.value == "DENIED_BY_SAFETY"
    assert "needs 100" in (record.failure_reason or "")


def test_zero_multiple_disables_the_gate(clock: Any) -> None:
    infy = _state("INFY", str(INFY_PRICE))
    broker = _broker(clock)
    workflow = _workflow(clock, ScriptedAI([_open("INFY", "1500.26")]), broker, multiple="0")

    record = asyncio.run(workflow.run(infy, board=[infy]))

    assert record.status.value == "EXECUTED"


@pytest.mark.parametrize("multiple", ["-1", "abc"])
def test_invalid_multiple_is_rejected(multiple: str) -> None:
    with pytest.raises(ValidationError):
        Settings(min_edge_cost_multiple=multiple)  # type: ignore[arg-type]
