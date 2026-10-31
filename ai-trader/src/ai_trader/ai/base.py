from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from ai_trader.domain.decisions import ProviderMetadata

T = TypeVar("T", bound=BaseModel)


class AIProvider(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier reported by the configured inference service."""

    @abstractmethod
    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]: ...

    async def aclose(self) -> None:  # noqa: B027 - the default implementation is intentionally concrete
        """Release provider resources. Providers without connections use this no-op."""
