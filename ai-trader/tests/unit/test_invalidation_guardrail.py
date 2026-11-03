"""Invalidation guardrail: the AI's own thesis-break level always holds.

A position trading at or below its entry plan's invalidation price is closed
deterministically — even when the AI review itself fails that cycle. No
invalidation recorded, no live mark, or an engaged kill switch all mean hands
off: never invent an exit level and never trade blind.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import OpenPositionBrief, PortfolioManagerAgent
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import CriticAssessment, PortfolioDecision, ProviderMetadata
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.portfolio import Position
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _meta(prompt: str = "portfolio_manager") -> ProviderMetadata:
    return ProviderMetadata(
        provider="test",
        model="test-model",
        prompt_name=prompt,
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


def _brief(**overrides: Any) -> OpenPositionBrief:
    base: dict[str, Any] = {
        "symbol": "GAIL",
        "quantity": 100,
        "average_entry_price": Decimal("175.00"),
        "last_price": Decimal("173.00"),
        "unrealized_inr": Decimal("-200"),
        "unrealized_pct": Decimal("-1.14"),
        "held_minutes": 60,
        "target_price": Decimal("178.00"),
        "invalidation_price": Decimal("174.00"),
        "entry_thesis": "Breakout.",
    }
    base.update(overrides)
    return OpenPositionBrief(**base)


class FakeLedger:
    """Records closes and answers open-context; never touches a database."""

    def __init__(self) -> None:
        self.closes: list = []
        self.context: dict[str, tuple[str | None, Any]] = {}

    async def record_fill(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def save_book(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_close(self, trade: Any) -> None:
        self.closes.append(trade)

    async def position_open_context(self, shard: int, symbol: str) -> tuple[str | None, Any]:
        return self.context.get(symbol, (None, None))


class ScriptedAI(AIProvider):
    def __init__(self, decisions: list[PortfolioDecision] | None = None, fail: bool = False) -> None:
        self.decisions = decisions or []
        self.fail = fail

    @property
    def model_name(self) -> str:
        return "scripted"

    async def structured_completion(self, *args: Any, **kwargs: Any) -> Any:
        if self.fail:
            from ai_trader.ai.remote_model import RemoteAIUnavailable

            raise RemoteAIUnavailable("boom")
        return self.decisions[0], _meta()


class AgreeingCritic(JevCritic):
    async def assess(self, proposal: Any, news: Any = None) -> Any:
        return (
            CriticAssessment(
                score=0.9,
                agrees=True,
                disagreement_signals=[],
                critique="fine",
                bounded_decisions=["APPROVE"],
            ),
            _meta("critic"),
        )


def _workflow(
    clock: Any,
    ai: ScriptedAI,
    broker: PaperBroker,
    ledger: FakeLedger,
    journal: InMemoryDecisionJournal | None = None,
) -> ShadowReviewWorkflow:
    broker.ledger = ledger  # type: ignore[assignment]
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v13"),
        AgreeingCritic(),
        broker,
        SafetyEnvelope(Settings(trading_enabled=True), KillSwitch(), clock),
        ExecutionService(broker),
        journal if journal is not None else InMemoryDecisionJournal(),
        clock,
    )


def _hold(broker: PaperBroker, symbol: str, quantity: int, average: str) -> None:
    broker.positions[symbol] = Position(
        symbol=symbol, quantity=quantity, average_entry_price=Decimal(average)
    )


def _entry_decision(entry_id: str, symbol: str, invalidation: str) -> Any:
    from ai_trader.domain.decisions import DecisionStatus

    now = datetime.now(UTC)
    return {
        "decision_id": entry_id,
        "correlation_id": "c",
        "event_id": "e",
        "timestamp": now,
        "status": DecisionStatus.EXECUTED,
        "input_context_hash": "h",
        "initial_ai_proposal": {
            "cash_percent": 0.0,
            "allocations": [],
            "proposals": [
                {
                    "proposal_id": "p-1",
                    "symbol": symbol,
                    "exchange": "NSE",
                    "segment": "EQ",
                    "action": "OPEN_POSITION",
                    "side": "BUY",
                    "capital_allocation_inr": "20000",
                    "quantity_shares": 100,
                    "confidence": 0.8,
                    "thesis": "Breakout.",
                    "invalidation_price": invalidation,
                }
            ],
            "market_summary": "m",
            "decision_summary": "d",
        },
        "initial_provider_metadata": {
            "provider": "t",
            "model": "m",
            "prompt_name": "p",
            "prompt_version": "v12",
        },
        "portfolio_before": {"cash": "100000", "positions": []},
    }


def test_breach_closes_even_when_ai_fails(clock: Any) -> None:
    import asyncio

    from ai_trader.journal.service import InMemoryDecisionJournal

    state = _state("GAIL", "173.00")
    broker = PaperBroker(Decimal("100000"), clock, {"GAIL": Decimal("173.00")})
    _hold(broker, "GAIL", 100, "175.00")
    ledger = FakeLedger()
    ledger.context["GAIL"] = ("entry-1", datetime.now(UTC))
    journal = InMemoryDecisionJournal()
    from ai_trader.journal.service import DecisionRecord

    asyncio.run(journal.save(DecisionRecord(**_entry_decision("entry-1", "GAIL", "174.00"))))
    workflow = _workflow(clock, ScriptedAI(fail=True), broker, ledger, journal)
    record = asyncio.run(
        workflow.run(state, board=[state], prices={"GAIL": Decimal("173.00")})
    )
    # The AI failed, so the decision is FAILED — but the guardrail still acted.
    assert record.status.value == "FAILED"
    assert broker.positions["GAIL"].quantity == 0
    assert len(ledger.closes) == 1
    assert ledger.closes[0].symbol == "GAIL"


def test_no_breach_leaves_position_alone(clock: Any) -> None:
    import asyncio

    broker = PaperBroker(Decimal("100000"), clock, {"GAIL": Decimal("176.00")})
    _hold(broker, "GAIL", 100, "175.00")
    workflow = _workflow(clock, ScriptedAI(fail=True), broker, FakeLedger())
    asyncio.run(workflow._enforce_invalidations([_brief(last_price=Decimal("176.00"))], "snap"))
    assert broker.positions["GAIL"].quantity == 100


def test_missing_mark_never_closes_blind(clock: Any) -> None:
    import asyncio

    broker = PaperBroker(Decimal("100000"), clock, {"GAIL": Decimal("173.00")})
    _hold(broker, "GAIL", 100, "175.00")
    workflow = _workflow(clock, ScriptedAI(fail=True), broker, FakeLedger())
    brief = _brief(last_price=None, unrealized_inr=None, unrealized_pct=None)
    asyncio.run(workflow._enforce_invalidations([brief], "snap"))
    assert broker.positions["GAIL"].quantity == 100


def test_missing_invalidation_never_invents_a_level(clock: Any) -> None:
    import asyncio

    broker = PaperBroker(Decimal("100000"), clock, {"GAIL": Decimal("150.00")})
    _hold(broker, "GAIL", 100, "175.00")
    workflow = _workflow(clock, ScriptedAI(fail=True), broker, FakeLedger())
    asyncio.run(workflow._enforce_invalidations([_brief(invalidation_price=None)], "snap"))
    assert broker.positions["GAIL"].quantity == 100


def test_kill_switch_holds_the_guardrail(clock: Any) -> None:
    import asyncio

    broker = PaperBroker(Decimal("100000"), clock, {"GAIL": Decimal("173.00")})
    _hold(broker, "GAIL", 100, "175.00")
    workflow = _workflow(clock, ScriptedAI(fail=True), broker, FakeLedger())
    assert isinstance(workflow.safety.kill_switch, KillSwitch)
    workflow.safety.kill_switch.enabled = True
    asyncio.run(workflow._enforce_invalidations([_brief()], "snap"))
    assert broker.positions["GAIL"].quantity == 100
