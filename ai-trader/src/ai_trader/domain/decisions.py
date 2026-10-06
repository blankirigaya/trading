from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_trader.domain.orders import OrderSide, OrderType


class PortfolioAction(StrEnum):
    OPEN_POSITION = "OPEN_POSITION"
    INCREASE_POSITION = "INCREASE_POSITION"
    REDUCE_POSITION = "REDUCE_POSITION"
    CLOSE_POSITION = "CLOSE_POSITION"
    HOLD = "HOLD"
    STAY_IN_CASH = "STAY_IN_CASH"


class DecisionStatus(StrEnum):
    CREATED = "CREATED"
    CRITIQUED = "CRITIQUED"
    RECONSIDERED = "RECONSIDERED"
    APPROVED_BY_AI = "APPROVED_BY_AI"
    DENIED_BY_SAFETY = "DENIED_BY_SAFETY"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    model: str
    request_id: str | None = None
    prompt_name: str
    prompt_version: str
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    temperature: float | None = None
    seed: int | None = None
    schema_version: str = "v1"
    provider_metadata: dict[str, object] = Field(default_factory=dict)


class TradeProposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    proposal_id: str
    symbol: str | None = None
    exchange: str = "NSE"
    segment: str = "EQ"
    action: PortfolioAction
    side: OrderSide | None = None
    capital_allocation_inr: Decimal | None = Field(default=None, gt=0)
    desired_entry_price: Decimal | None = Field(default=None, gt=0)
    order_type: OrderType = OrderType.MARKET
    invalidation_price: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)
    expected_holding_minutes: int | None = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1)
    thesis: str = Field(min_length=1)
    evidence: list[str]
    conditions_that_change_thesis: list[str]

    @model_validator(mode="after")
    def require_order_fields_for_trade(self) -> "TradeProposal":
        trade_actions = {PortfolioAction.OPEN_POSITION, PortfolioAction.INCREASE_POSITION}
        if self.action in trade_actions and (
            self.symbol is None or self.side is None or self.capital_allocation_inr is None
        ):
            raise ValueError("trade proposals require symbol, side, and capital allocation")
        return self


class DesiredPortfolioAllocation(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    allocation_percent: float = Field(ge=0, le=100)
    thesis: str
    confidence: float = Field(ge=0, le=1)


class PortfolioDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    cash_percent: float = Field(ge=0, le=100)
    allocations: list[DesiredPortfolioAllocation]
    proposals: list[TradeProposal]
    market_summary: str
    decision_summary: str


class CriticAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)
    score: float = Field(ge=0, le=1)
    agrees: bool
    disagreement_signals: list[str]
    critique: str
    bounded_decisions: list[str]


class FinalAIDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision_id: str
    correlation_id: str
    approved: bool
    decision_summary: str
    proposals: list[TradeProposal]
