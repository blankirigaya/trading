from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ai_trader.domain.decisions import FinalAIDecision, TradeProposal
from ai_trader.domain.market import MarketTick
from ai_trader.domain.orders import Fill, Order


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str
    correlation_id: str
    occurred_at: datetime
    raw_payload: dict[str, object] = Field(default_factory=dict)
    event_name: ClassVar[str] = "event"


class MarketTickReceived(Event):
    event_name: ClassVar[str] = "market_tick_received"
    tick: MarketTick


class PortfolioReviewRequested(Event):
    event_name: ClassVar[str] = "portfolio_review_requested"
    source_event_id: str
    tick: MarketTick


class AIProposalCreated(Event):
    event_name: ClassVar[str] = "ai_proposal_created"
    proposal: TradeProposal


class FinalAIDecisionCreated(Event):
    event_name: ClassVar[str] = "final_ai_decision_created"
    decision: FinalAIDecision


class OrderSubmitted(Event):
    event_name: ClassVar[str] = "order_submitted"
    order: Order


class OrderFilled(Event):
    event_name: ClassVar[str] = "order_filled"
    fill: Fill


class SafetyViolation(Event):
    event_name: ClassVar[str] = "safety_violation"
    decision_id: str
    reason: str


class ExecutionAdjustmentRequired(Event):
    event_name: ClassVar[str] = "execution_adjustment_required"
    decision_id: str
    proposal_id: str
    reason: str
