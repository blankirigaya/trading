"""Deterministic risk policy: ATR plans, reward/risk, sizing, and protective exits.

These rules are the part of the system that must not depend on the model being
right. Each test pins one economic promise: plans are measured in the symbol's
own volatility, losers are smaller than winners, size follows the stop, and
winners are not given back.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.charges import ChargeRates
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
from ai_trader.risk.entry_plan import (
    RiskRules,
    evaluate_entry_plan,
    size_entry_by_risk,
)
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope
from ai_trader.screening.indicators import atr, true_ranges

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)


def _meta() -> ProviderMetadata:
    return ProviderMetadata(
        provider="test", model="test", prompt_name="portfolio_manager", prompt_version="v15"
    )


def _candles(closes: list[str], spread: str = "1.00") -> list[Candle]:
    candles = []
    spread_value = Decimal(spread)
    for index, close in enumerate(closes):
        price = Decimal(close)
        candles.append(
            Candle(
                start_timestamp=NOW + timedelta(minutes=index),
                end_timestamp=NOW + timedelta(minutes=index + 1),
                open=price,
                high=price + spread_value / 2,
                low=price - spread_value / 2,
                close=price,
                volume=1000,
            )
        )
    return candles


# ---------- ATR ----------


def test_atr_is_the_average_range_of_recent_bars() -> None:
    candles = _candles(["100", "100", "100", "100"], spread="2.00")
    assert atr(candles, period=3) == Decimal("2.00")


def test_atr_uses_gaps_when_they_exceed_the_bar_range() -> None:
    candles = _candles(["100", "110"], spread="1.00")
    # Gap from the 100 close to the 110.5 high is the true range (10.5), not
    # the 1.00 bar range.
    assert true_ranges(candles)[0] == Decimal("10.5")


def test_atr_returns_none_without_enough_history() -> None:
    assert atr([], 14) is None
    assert atr(_candles(["100"]), 14) is None


# ---------- reward/risk ----------


def _plan(
    *,
    entry: str = "100",
    target: str | None = "110",
    invalidation: str | None = "95",
) -> Any:
    return evaluate_entry_plan(
        entry_price=Decimal(entry),
        target_price=Decimal(target) if target else None,
        invalidation_price=Decimal(invalidation) if invalidation else None,
        quantity=100,
        rates=ChargeRates(),
        min_cost_multiple=Decimal("2.5"),
        min_reward_risk=Decimal("2.0"),
    )


def test_plan_with_reward_at_least_twice_risk_passes() -> None:
    # 10 up vs 5 down = 2.0 exactly, and 1000 INR gross clears charges.
    assert _plan().allowed is True


def test_plan_with_poor_reward_risk_is_refused() -> None:
    check = _plan(target="104", invalidation="97")
    assert check.allowed is False
    assert "reward/risk" in (check.reason or "")


def test_invalidation_at_or_above_entry_is_refused() -> None:
    check = _plan(invalidation="100")
    assert check.allowed is False
    assert "no bounded loss" in (check.reason or "")


def test_plan_without_invalidation_is_left_alone() -> None:
    """Historical and test proposals may predate the requirement."""
    assert _plan(invalidation=None).allowed is True


# ---------- sizing ----------


def test_size_is_capped_by_the_risk_budget() -> None:
    sizing = size_entry_by_risk(
        equity=Decimal("100000"),
        risk_per_trade_pct=Decimal("0.5"),
        entry_price=Decimal("100"),
        invalidation_price=Decimal("90"),
        requested_quantity=1000,
    )
    # 500 INR budget / 10 INR per share = 50 shares.
    assert sizing is not None
    assert sizing.quantity == 50
    assert sizing.capped is True


def test_smaller_requested_size_is_respected() -> None:
    sizing = size_entry_by_risk(
        equity=Decimal("100000"),
        risk_per_trade_pct=Decimal("0.5"),
        entry_price=Decimal("100"),
        invalidation_price=Decimal("90"),
        requested_quantity=10,
    )
    assert sizing is not None
    assert sizing.quantity == 10
    assert sizing.capped is False


def test_wide_stop_means_a_zero_share_position_when_budget_is_too_small() -> None:
    sizing = size_entry_by_risk(
        equity=Decimal("100000"),
        risk_per_trade_pct=Decimal("0.5"),
        entry_price=Decimal("1000"),
        invalidation_price=Decimal("400"),
        requested_quantity=10,
    )
    assert sizing is not None
    assert sizing.quantity == 0


def test_sizing_is_disabled_without_an_invalidation() -> None:
    assert (
        size_entry_by_risk(
            equity=Decimal("100000"),
            risk_per_trade_pct=Decimal("0.5"),
            entry_price=Decimal("100"),
            invalidation_price=None,
            requested_quantity=10,
        )
        is None
    )


# ---------- workflow: sizing and exits ----------


def _state(symbol: str, price: str, candles: list[Candle] | None = None) -> MarketState:
    tick = MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal(price),
        exchange_timestamp=NOW,
        received_timestamp=NOW,
        processed_timestamp=NOW,
        raw_payload={},
    )
    candle = Candle(
        start_timestamp=NOW,
        end_timestamp=NOW,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=NOW,
        last_price=Decimal(price),
        previous_close=Decimal(price),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=candles or [candle],
    )


def _open(symbol: str, target: str, invalidation: str, qty: int = 100) -> PortfolioDecision:
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
                quantity_shares=qty,
                desired_entry_price=Decimal("100"),
                target_price=Decimal(target),
                invalidation_price=Decimal(invalidation),
                confidence=0.8,
                thesis="ATR breakout.",
            )
        ],
        market_summary="m",
        decision_summary="d",
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
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return decision, _meta()


class AgreeingCritic(JevCritic):
    async def assess(self, proposal: Any, news: Any = None) -> Any:
        return (
            CriticAssessment(
                score=0.95,
                agrees=True,
                disagreement_signals=[],
                critique="fine",
                bounded_decisions=["APPROVE"],
            ),
            _meta(),
        )


def _workflow(clock: Any, ai: ScriptedAI, broker: PaperBroker, **rules: Any) -> ShadowReviewWorkflow:
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v15"),
        AgreeingCritic(),
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        InMemoryDecisionJournal(),
        clock,
        jev_min_entry_score=0.0,
        risk_rules=RiskRules(charge_rates=ChargeRates(), **rules),
    )


def test_workflow_resizes_an_oversized_entry_to_the_risk_budget(clock: Any) -> None:
    state = _state("INFY", "100")
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("100")})
    workflow = _workflow(
        clock,
        ScriptedAI([_open("INFY", target="110", invalidation="95", qty=1000)]),
        broker,
    )

    record = asyncio.run(workflow.run(state, board=[state]))

    assert record.status.value == "EXECUTED"
    # 0.5% of ~100000 = 500 INR budget, 5 INR stop distance -> 100 shares,
    # down from the 1000 the model asked for.
    assert record.orders_generated[0].request.quantity == 100


def _hold_decision() -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=100,
        allocations=[],
        proposals=[],
        market_summary="m",
        decision_summary="hold",
    )


def _held_broker(clock: Any, symbol: str, price: str) -> PaperBroker:
    broker = PaperBroker(Decimal("100000"), clock, {"INFY": Decimal("100")})
    broker.positions[symbol] = Position(
        symbol=symbol, quantity=10, average_entry_price=Decimal("100")
    )
    broker.set_price(symbol, Decimal(price))
    return broker


def test_trailing_stop_locks_a_gain_after_a_pullback(clock: Any) -> None:
    primary = _state("INFY", "100")
    held = _state("LT", "108", _candles(["108"] * 15, spread="2.00"))
    broker = _held_broker(clock, "LT", "108")
    workflow = _workflow(clock, ScriptedAI([_hold_decision()]), broker)
    # Peak 112 seen earlier, then 108 with ATR 2.0: trail level = 112 - 0.5*2 = 111.
    workflow._peaks["LT"] = Decimal("112")

    asyncio.run(
        workflow.run(primary, board=[primary, held], prices={"LT": Decimal("108")})
    )

    # Triggered by the trail level (111) and filled at the live mark (108):
    # the gain is locked rather than given all the way back.
    assert broker.positions["LT"].quantity == 0
    assert broker.positions["LT"].realized_pnl == Decimal("80.00")


def test_trailing_stop_does_not_cut_a_normal_pullback(clock: Any) -> None:
    primary = _state("INFY", "100")
    held = _state("LT", "111.5", _candles(["111.5"] * 15, spread="2.00"))
    broker = _held_broker(clock, "LT", "111.5")
    workflow = _workflow(clock, ScriptedAI([_hold_decision()]), broker)
    workflow._peaks["LT"] = Decimal("112")

    asyncio.run(
        workflow.run(primary, board=[primary, held], prices={"LT": Decimal("111.5")})
    )

    assert broker.positions["LT"].quantity == 10


def test_trailing_stop_falls_back_to_giving_back_half_the_gain(clock: Any) -> None:
    """FYERS publishes no ATR, so the trail must still work without one."""
    primary = _state("INFY", "100")
    held = _state("LT", "105")  # single candle: ATR is unavailable
    broker = _held_broker(clock, "LT", "105")
    workflow = _workflow(clock, ScriptedAI([_hold_decision()]), broker)
    workflow._peaks["LT"] = Decimal("112")

    # Peak gain was 12; giving back half leaves a trail level of 106.
    asyncio.run(workflow.run(primary, board=[primary, held], prices={"LT": Decimal("105")}))

    assert broker.positions["LT"].quantity == 0


def test_trailing_stop_never_realises_a_loss(clock: Any) -> None:
    primary = _state("INFY", "100")
    held = _state("LT", "95", _candles(["95"] * 15, spread="2.00"))
    broker = _held_broker(clock, "LT", "95")
    workflow = _workflow(clock, ScriptedAI([_hold_decision()]), broker)
    workflow._peaks["LT"] = Decimal("112")

    asyncio.run(workflow.run(primary, board=[primary, held], prices={"LT": Decimal("95")}))

    # Below entry: only invalidation may close it, never the trail.
    assert broker.positions["LT"].quantity == 10


def test_exit_floor_keeps_a_dust_winner(clock: Any) -> None:
    state = _state("INFY", "100.01")
    broker = _held_broker(clock, "LT", "100.01")
    decision = PortfolioDecision(
        cash_percent=100,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="x",
                symbol="LT",
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                quantity_shares=10,
                confidence=0.9,
                thesis="bank it",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )
    workflow = _workflow(clock, ScriptedAI([decision]), broker)

    record = asyncio.run(workflow.run(state, board=[state], prices={"LT": Decimal("100.01")}))

    assert record.status.value == "DENIED_BY_SAFETY"
    assert "exit floor" in (record.failure_reason or "")
    assert "LT" in broker.positions


def test_exit_floor_never_blocks_a_losing_exit(clock: Any) -> None:
    state = _state("INFY", "99")
    broker = _held_broker(clock, "LT", "99")
    decision = PortfolioDecision(
        cash_percent=100,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="x",
                symbol="LT",
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                quantity_shares=10,
                confidence=0.9,
                thesis="cut it",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )
    workflow = _workflow(clock, ScriptedAI([decision]), broker)

    record = asyncio.run(workflow.run(state, board=[state], prices={"LT": Decimal("99")}))

    assert record.status.value == "EXECUTED"


@pytest.mark.parametrize("field", ["min_reward_risk", "risk_per_trade_pct", "trail_atr_multiple"])
def test_negative_risk_settings_are_rejected(field: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(**{field: "-1"})  # type: ignore[arg-type]


def test_entry_only_review_does_not_execute_exits(clock: Any) -> None:
    """An entries-only scan must leave exits to the automatic rules."""
    state = _state("INFY", "100")
    broker = _held_broker(clock, "LT", "120")
    decision = PortfolioDecision(
        cash_percent=100,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="x",
                symbol="LT",
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                quantity_shares=10,
                confidence=0.9,
                thesis="bank it",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )
    workflow = _workflow(clock, ScriptedAI([decision]), broker)

    record = asyncio.run(
        workflow.run(state, board=[state], prices={"LT": Decimal("120")}, entry_only=True)
    )

    assert record.orders_generated == []
    assert broker.positions["LT"].quantity == 10
