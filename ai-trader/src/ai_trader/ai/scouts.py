from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ai_trader.domain.market import MarketTick


class ScoutPriority(StrEnum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"


class ScoutResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    priority: ScoutPriority
    summary: str


class MarketScout(ABC):
    @abstractmethod
    async def evaluate(self, market_snapshot: MarketTick) -> ScoutResult: ...


class MockMarketScout(MarketScout):
    async def evaluate(self, market_snapshot: MarketTick) -> ScoutResult:
        return ScoutResult(priority=ScoutPriority.IMPORTANT, summary="Mock scout requests review.")
