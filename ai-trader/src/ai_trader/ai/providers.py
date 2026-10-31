"""Central selection for configured AI providers.

Trading code depends only on `AIProvider`. Adding or changing a provider should change
this factory, not every workflow, session, probe, or CLI that needs inference.
"""

from ai_trader.ai.base import AIProvider
from ai_trader.ai.bedrock import BedrockAIProvider
from ai_trader.ai.remote_model import AIHealth, OpenAICompatibleAIProvider
from ai_trader.config import Settings

SUPPORTED_AI_PROVIDERS = ("remote_openai_compatible", "bedrock")


def build_ai_provider(
    settings: Settings, health: AIHealth | None = None, *, cadence: bool = False
) -> AIProvider:
    """Return the provider selected by `AI_PROVIDER`, validating its configuration.

    With ``cadence=True`` and a Bedrock backend, routine reviews use
    ``BEDROCK_CADENCE_MODEL_ID`` when set (falling back to the main model and
    rates otherwise). News analysis and anything else keeps the main model.
    """
    provider_name = settings.ai_provider.strip().lower()
    if provider_name == "bedrock":
        if cadence and settings.bedrock_cadence_model_id.strip():
            return BedrockAIProvider.from_settings(
                settings,
                health=health,
                model_id=settings.bedrock_cadence_model_id,
                input_usd_per_million_tokens=(
                    settings.bedrock_cadence_input_usd_per_million_tokens
                    if settings.bedrock_cadence_input_usd_per_million_tokens is not None
                    else settings.bedrock_input_usd_per_million_tokens
                ),
                output_usd_per_million_tokens=(
                    settings.bedrock_cadence_output_usd_per_million_tokens
                    if settings.bedrock_cadence_output_usd_per_million_tokens is not None
                    else settings.bedrock_output_usd_per_million_tokens
                ),
            )
        return BedrockAIProvider.from_settings(settings, health=health)
    if provider_name == "remote_openai_compatible":
        return OpenAICompatibleAIProvider(
            settings.ai_base_url,
            settings.ai_model,
            settings.ai_api_key,
            settings.ai_timeout_seconds,
            settings.ai_max_concurrent_requests,
            health=health,
        )
    raise ValueError(
        f"Unsupported AI_PROVIDER: {settings.ai_provider!r}. "
        f"Supported providers: {', '.join(SUPPORTED_AI_PROVIDERS)}"
    )


__all__ = ["SUPPORTED_AI_PROVIDERS", "build_ai_provider"]
