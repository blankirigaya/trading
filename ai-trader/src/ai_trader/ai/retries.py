from typing import TypeVar

from pydantic import BaseModel

from ai_trader.ai.base import AIProvider
from ai_trader.domain.decisions import ProviderMetadata

T = TypeVar("T", bound=BaseModel)


class StructuredCompletionFailed(RuntimeError):
    """The model repeatedly returned output that failed schema validation."""


class RetryingAIProvider(AIProvider):
    """Retries only schema-validation failures. Network failures stay fail-closed immediately."""

    def __init__(self, provider: AIProvider, max_retries: int) -> None:
        self.provider = provider
        self.max_retries = max_retries

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]:
        prompt = user_prompt
        for attempt in range(self.max_retries + 1):
            try:
                result, metadata = await self.provider.structured_completion(
                    system_prompt,
                    prompt,
                    response_model,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                )
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == self.max_retries:
                    raise StructuredCompletionFailed(
                        "AI response failed schema validation"
                    ) from exc
                prompt = (
                    f"{user_prompt}\nYour prior output failed schema validation. "
                    "Return only valid JSON for the requested schema."
                )
                continue
            updated = metadata.model_copy(
                update={
                    "provider_metadata": {
                        **metadata.provider_metadata,
                        "retry_count": attempt,
                    }
                }
            )
            return result, updated
        raise AssertionError("unreachable")
