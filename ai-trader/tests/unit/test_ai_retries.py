from pydantic import BaseModel

from ai_trader.ai.base import AIProvider
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.domain.decisions import ProviderMetadata


class Response(BaseModel):
    value: int


class FlakyProvider(AIProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "flaky-test-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.calls += 1
        if self.calls == 1:
            response_model.model_validate({"value": "invalid"})
        return Response(value=1), ProviderMetadata(
            provider="test", model="test", prompt_name=prompt_name, prompt_version=prompt_version
        )


async def test_invalid_structured_output_is_retried() -> None:
    provider = FlakyProvider()
    response, _ = await RetryingAIProvider(provider, 2).structured_completion(
        "system", "user", Response, prompt_name="test", prompt_version="v1"
    )
    assert response.value == 1
    assert provider.calls == 2
