import json
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel

from ai_trader.ai.base import AIProvider
from ai_trader.domain.decisions import ProviderMetadata

T = TypeVar("T", bound=BaseModel)


class MockAIProvider(AIProvider):
    def __init__(self, response: BaseModel, model: str = "mock-portfolio-v1") -> None:
        self.response = response
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]:
        start = perf_counter()
        parsed = response_model.model_validate(json.loads(self.response.model_dump_json()))
        metadata = ProviderMetadata(
            provider="mock",
            model=self.model,
            request_id=None,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=round((perf_counter() - start) * 1000),
        )
        return parsed, metadata
