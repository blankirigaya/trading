"""Every new entry must carry a target, or the edge gate cannot bind.

Two properties matter: the rule is enforced on fresh AI output, and a missing
target is treated as a schema fault so the retry wrapper names the field instead
of silently accepting an unverifiable trade.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ai_trader.ai.base import AIProvider
from ai_trader.ai.entry_plan import EntryPlanRequired
from ai_trader.ai.errors import SchemaValidationError
from ai_trader.ai.retries import RetryingAIProvider, StructuredCompletionFailed
from ai_trader.domain.decisions import (
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.orders import OrderSide


def _meta() -> ProviderMetadata:
    return ProviderMetadata(
        provider="test", model="test", prompt_name="portfolio_manager", prompt_version="v13"
    )


def _entry(target: str | None, invalidation: str | None = "1490") -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=0.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="p-1",
                symbol="INFY",
                action=PortfolioAction.OPEN_POSITION,
                side=OrderSide.BUY,
                capital_allocation_inr=Decimal("20000"),
                quantity_shares=10,
                target_price=Decimal(target) if target is not None else None,
                invalidation_price=Decimal(invalidation) if invalidation is not None else None,
                confidence=0.7,
                thesis="board pick",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )


def _exit() -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=100.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="x-1",
                symbol="INFY",
                action=PortfolioAction.CLOSE_POSITION,
                side=OrderSide.SELL,
                quantity_shares=10,
                confidence=0.8,
                thesis="exit",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )


def _hold() -> PortfolioDecision:
    return PortfolioDecision(
        cash_percent=100.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="h-1",
                action=PortfolioAction.HOLD,
                confidence=0.5,
                thesis="wait",
            )
        ],
        market_summary="m",
        decision_summary="d",
    )


class ScriptedProvider(AIProvider):
    def __init__(self, decisions: list[PortfolioDecision]) -> None:
        self.decisions = decisions
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    async def structured_completion(
        self, system_prompt: Any, user_prompt: Any, response_model: Any, **kwargs: Any
    ) -> Any:
        self.prompts.append(user_prompt)
        return self.decisions[min(len(self.prompts) - 1, len(self.decisions) - 1)], _meta()


async def test_entry_without_target_is_rejected() -> None:
    wrapped = EntryPlanRequired(ScriptedProvider([_entry(None)]))
    with pytest.raises(SchemaValidationError) as raised:
        await wrapped.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v13"
        )
    assert "target_price" in raised.value.detail
    assert "target_price" in str(raised.value)


async def test_entry_with_target_passes() -> None:
    decision, _ = await EntryPlanRequired(ScriptedProvider([_entry("1520")])).structured_completion(
        "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v13"
    )
    assert decision.proposals[0].target_price == Decimal("1520")


async def test_entry_without_invalidation_is_rejected() -> None:
    wrapped = EntryPlanRequired(ScriptedProvider([_entry("1520", invalidation=None)]))
    with pytest.raises(SchemaValidationError) as raised:
        await wrapped.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v14"
        )
    assert "invalidation_price" in raised.value.detail


@pytest.mark.parametrize("decision", [_exit(), _hold()])
async def test_non_entry_decisions_need_no_target(decision: PortfolioDecision) -> None:
    """Exits and holds must never be blocked for omitting a target."""
    parsed, _ = await EntryPlanRequired(ScriptedProvider([decision])).structured_completion(
        "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v13"
    )
    assert parsed is decision


async def test_missing_target_is_retried_with_the_field_named() -> None:
    provider = ScriptedProvider([_entry(None), _entry("1520")])
    wrapped = RetryingAIProvider(EntryPlanRequired(provider), 1)

    decision, metadata = await wrapped.structured_completion(
        "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v13"
    )

    assert decision.proposals[0].target_price == Decimal("1520")
    assert "target_price" in provider.prompts[1]
    assert metadata.provider_metadata["retry_count"] == 1


async def test_persistently_missing_target_fails_closed_with_detail() -> None:
    provider = ScriptedProvider([_entry(None)])
    wrapped = RetryingAIProvider(EntryPlanRequired(provider), 1)

    with pytest.raises(StructuredCompletionFailed) as raised:
        await wrapped.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="p", prompt_version="v13"
        )
    assert "target_price" in str(raised.value)
