import pytest
from pydantic import BaseModel

from ai_trader.ai.base import AIProvider
from ai_trader.ai.errors import SchemaValidationError
from ai_trader.ai.retries import RetryingAIProvider, StructuredCompletionFailed
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


class AlwaysInvalidProvider(AIProvider):
    """Raises a typed schema failure carrying the offending field."""

    detail = "proposals.0.quantity_shares: Input should be a valid integer"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "always-invalid"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.prompts.append(user_prompt)
        raise SchemaValidationError(self.detail)


async def test_retry_prompt_names_the_field_that_failed() -> None:
    provider = AlwaysInvalidProvider()
    with pytest.raises(StructuredCompletionFailed):
        await RetryingAIProvider(provider, 1).structured_completion(
            "system", "user", Response, prompt_name="test", prompt_version="v1"
        )
    # First attempt uses the original prompt; the retry tells the model exactly
    # what was wrong, so it does not repeat the same omission.
    assert provider.prompts[0] == "user"
    assert "quantity_shares" in provider.prompts[1]


async def test_exhausted_retries_report_the_schema_detail() -> None:
    provider = AlwaysInvalidProvider()
    with pytest.raises(StructuredCompletionFailed) as raised:
        await RetryingAIProvider(provider, 1).structured_completion(
            "system", "user", Response, prompt_name="test", prompt_version="v1"
        )
    assert "quantity_shares" in str(raised.value)
    assert provider.prompts[-1] != "user"
