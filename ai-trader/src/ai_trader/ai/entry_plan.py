"""Require an explicit target on every new entry proposal.

The minimum-edge gate is only as strong as the plan it measures: a proposal with
no ``target_price`` cannot be checked, so it would slip past the gate. Prompt
text alone is not a guarantee, so this decorator enforces the rule on the AI's
output and raises a schema fault — which the surrounding retry wrapper turns
into one corrected attempt.

Deliberately a boundary check rather than a Pydantic field requirement: the
domain model must still deserialize historical decisions recorded before the
rule existed. Only fresh output is held to it.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ai_trader.ai.base import AIProvider
from ai_trader.ai.errors import SchemaValidationError
from ai_trader.domain.decisions import PortfolioAction, PortfolioDecision, ProviderMetadata

T = TypeVar("T", bound=BaseModel)

_ENTRY_ACTIONS = frozenset(
    {PortfolioAction.OPEN_POSITION, PortfolioAction.INCREASE_POSITION}
)


class EntryPlanRequired(AIProvider):
    """Provider decorator: entry proposals must state target_price.

    ``invalidation_price`` is deliberately *not* required yet. Only 30% of
    historical entries carry one, so demanding it now would trade a lot of
    review cycles for a field nothing consumes until risk-based sizing lands.
    """

    def __init__(self, provider: AIProvider) -> None:
        self.provider = provider

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def provider_label(self) -> str:
        return self.provider.provider_label

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]:
        result, metadata = await self.provider.structured_completion(
            system_prompt,
            user_prompt,
            response_model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )
        if isinstance(result, PortfolioDecision):
            missing = self._missing_plan_fields(result)
            if missing:
                raise SchemaValidationError("; ".join(missing))
        return result, metadata

    @staticmethod
    def _missing_plan_fields(decision: PortfolioDecision) -> list[str]:
        missing: list[str] = []
        for index, proposal in enumerate(decision.proposals):
            if proposal.action not in _ENTRY_ACTIONS:
                continue
            if proposal.target_price is None:
                missing.append(
                    f"proposals.{index}.target_price: required for "
                    f"{proposal.action.value}; the entry cannot be cost-checked without it"
                )
        return missing
