from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ai_trader.domain.decisions import (
    CriticAssessment,
    DecisionStatus,
    FinalAIDecision,
    PortfolioDecision,
    ProviderMetadata,
)
from ai_trader.domain.orders import Order
from ai_trader.domain.portfolio import PortfolioState


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision_id: str
    correlation_id: str
    event_id: str
    timestamp: datetime
    status: DecisionStatus
    input_context_hash: str
    initial_ai_proposal: PortfolioDecision
    initial_provider_metadata: ProviderMetadata
    jev_critique: CriticAssessment | None = None
    jev_provider_metadata: ProviderMetadata | None = None
    final_ai_decision: FinalAIDecision | None = None
    portfolio_before: PortfolioState
    portfolio_after: PortfolioState | None = None
    orders_generated: list[Order] = Field(default_factory=list)
    market_snapshot_id: str | None = None
    market_timestamp: datetime | None = None
    instrument_key: str | None = None
    symbol: str | None = None
    reconsideration_requested: bool = False
    reconsideration_performed: bool = False
    safety_allowed: bool | None = None
    safety_reason: str | None = None
    failure_reason: str | None = None
    human_notes: str | None = None


class DecisionJournal(ABC):
    @abstractmethod
    async def save(self, record: DecisionRecord) -> None: ...

    @abstractmethod
    async def get(self, decision_id: str) -> DecisionRecord | None: ...

    @abstractmethod
    async def list_recent(self) -> list[DecisionRecord]: ...


class InMemoryDecisionJournal(DecisionJournal):
    def __init__(self) -> None:
        self.records: dict[str, DecisionRecord] = {}

    async def save(self, record: DecisionRecord) -> None:
        self.records[record.decision_id] = record

    async def get(self, decision_id: str) -> DecisionRecord | None:
        return self.records.get(decision_id)

    async def list_recent(self) -> list[DecisionRecord]:
        return list(self.records.values())
