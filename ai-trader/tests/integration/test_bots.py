"""Bots endpoint: per-shard view of reviewers, books, and recent decisions."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import DisabledJevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.fyers_session import ReviewShard, ShadowSession
from ai_trader.application.runtime import ManagedShadowRuntime
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import (
    DecisionStatus,
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.orders import OrderSide
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import DecisionRecord, InMemoryDecisionJournal
from ai_trader.main import create_app
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def _tick(symbol: str, price: str, at: datetime) -> MarketTick:
    return MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal(price),
        exchange_timestamp=at,
        received_timestamp=at,
        processed_timestamp=at,
        raw_payload={},
    )


def _state(symbol: str, price: str, at: datetime) -> MarketState:
    tick = _tick(symbol, price, at)
    candle = Candle(
        start_timestamp=at,
        end_timestamp=at,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=at,
        last_price=Decimal(price),
        previous_close=Decimal(price),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


class StubFeed:
    def __init__(self, states: list[MarketState]) -> None:
        self._states = {s.instrument.symbol: s for s in states}
        self.socket = self

    async def run(self) -> None:
        import asyncio

        await asyncio.sleep(3600)

    async def stop(self) -> None:
        self.stopped = True

    def states(self) -> list[MarketState]:
        return list(self._states.values())


class HoldingAI(AIProvider):
    @property
    def model_name(self) -> str:
        return "hold-model"

    async def structured_completion(self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version):
        return PortfolioDecision(
            cash_percent=100.0,
            allocations=[],
            market_summary="m",
            decision_summary="hold",
            proposals=[],
        ), ProviderMetadata(
            provider="test",
            model=self.model_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )


def _session_with_shards(symbols: list[str], price: str = "100") -> ShadowSession:
    from ai_trader.domain.common import SystemClock as Clock

    clock = Clock()
    now = datetime.now(UTC)
    settings = Settings(trading_enabled=False, news_enabled=False)
    journal = InMemoryDecisionJournal()
    session = ShadowSession(
        settings=settings,
        feed=StubFeed([_state(s, price, now) for s in symbols]),
        journal=journal,
        clock=clock,
        interval_seconds=0,
        kill_switch=KillSwitch(),
    )
    for index, symbol in enumerate(symbols):
        ai = HoldingAI()
        broker = PaperBroker(Decimal("10000"), clock)
        workflow = ShadowReviewWorkflow(
            PortfolioManagerAgent(ai, "v9"),
            DisabledJevCritic(),
            broker,
            SafetyEnvelope(settings, session.kill_switch, clock),
            ExecutionService(broker),
            journal,
            clock,
        )
        session.shards.append(
            ReviewShard(
                index=index,
                symbols=(f"NSE:{symbol}-EQ",),
                broker=broker,
                workflow=workflow,
                provider=ai,
            )
        )
    return session


def test_bots_empty_without_session() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/bots").json() == []


def test_bots_lists_each_shard_with_book_and_decisions() -> None:

    with TestClient(create_app()) as client:
        app = client.app
        session = _session_with_shards(["INFY", "TCS"], price="110")
        app.state.container.runtime = ManagedShadowRuntime(session)
        app.state.container.journal = session.journal
        app.state.container.record_market_state(session.feed.states())

        # One open paper position on the first shard, none on the second.
        broker = session.shards[0].broker
        broker.positions["INFY"] = Position(symbol="INFY", quantity=10, average_entry_price=Decimal("100"))
        broker.set_price("INFY", Decimal("110"))

        record = DecisionRecord(
            decision_id="d1",
            correlation_id="c",
            event_id="e",
            timestamp=datetime.now(UTC),
            status=DecisionStatus.EXECUTED,
            input_context_hash="h",
            initial_ai_proposal=PortfolioDecision(
                cash_percent=0.0,
                allocations=[],
                proposals=[
                    TradeProposal(
                        proposal_id="p1",
                        symbol="INFY",
                        action=PortfolioAction.OPEN_POSITION,
                        side=OrderSide.BUY,
                        capital_allocation_inr=Decimal("1000"),
                        quantity_shares=9,
                        confidence=0.7,
                        thesis="test entry",
                    )
                ],
                market_summary="m",
                decision_summary="d",
            ),
        initial_provider_metadata=ProviderMetadata(
            provider="t", model="m", prompt_name="p", prompt_version="v1"
        ),
        portfolio_before=PortfolioState(cash=Decimal("1")),
        symbol="INFY",
    )
        import asyncio

        asyncio.run(session.journal.save(record))

        # A HOLD review on the same shard's tick carries no trade: it must not
        # clutter the bot's recent decisions.
        hold_record = record.model_copy(
            update={
                "decision_id": "d2",
                "status": DecisionStatus.APPROVED_BY_AI,
                "initial_ai_proposal": PortfolioDecision(
                    cash_percent=100.0,
                    allocations=[],
                    proposals=[
                        TradeProposal(
                            proposal_id="p2",
                            action=PortfolioAction.HOLD,
                            confidence=0.6,
                            thesis="waiting for a setup",
                        )
                    ],
                    market_summary="m",
                    decision_summary="d",
                ),
            }
        )
        asyncio.run(session.journal.save(hold_record))

        body = client.get("/bots").json()
        assert [b["name"] for b in body] == ["Bot 1", "Bot 2"]
        assert body[0]["symbols"] == ["INFY"]
        assert body[0]["cash"] == "10000"
        assert body[0]["reviews"] == 0
        assert body[0]["ai_model"] == "hold-model"
        assert len(body[0]["positions"]) == 1
        assert body[0]["positions"][0]["symbol"] == "INFY"
        assert body[0]["positions"][0]["ltp"] == "110"
        assert body[0]["recent_decisions"][0]["decision_id"] == "d1"
        assert body[0]["recent_decisions"][0]["symbol"] == "INFY"
        assert [d["decision_id"] for d in body[0]["recent_decisions"]] == ["d1"]
        assert body[1]["positions"] == []
        assert body[1]["recent_decisions"] == []
