"""Amazon Bedrock implementation of the shared AI-provider interface.

This provider uses the Bedrock Runtime Converse API with forced tool use. The native
Pydantic response schema is supplied as the Converse tool schema, then the tool result
is validated locally with the same Pydantic models used by every other provider. That
keeps response semantics identical when the transport changes.

AWS credentials are resolved only through the standard boto3 credential chain. This
module never accepts, logs, persists, or returns secrets.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, TypeVar

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    InvalidRegionError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
    SSLError,
    UnknownRegionError,
)
from pydantic import BaseModel, ValidationError

from ai_trader.ai.base import AIProvider
from ai_trader.ai.errors import SchemaValidationError, summarize_validation_errors
from ai_trader.ai.remote_model import AIHealth, RemoteAIUnavailable
from ai_trader.config import Settings
from ai_trader.domain.decisions import ProviderMetadata

T = TypeVar("T", bound=BaseModel)
BedrockClient = Any
BedrockClientFactory = Callable[[str | None, Config], BedrockClient]
SleepFunction = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)

_TOOL_NAME = "return_structured_decision"
_CHARS_PER_TOKEN = 4
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "ThrottledException",
        "TooManyRequestsException",
        "RequestTimeout",
        "RequestTimeoutException",
        "TimeoutError",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalError",
        "InternalFailure",
        "InternalServerException",
        "BadGateway",
        "BadGatewayException",
        "CapacityNotAvailable",
    }
)
_CREDENTIAL_ERRORS = (
    NoCredentialsError,
    PartialCredentialsError,
    CredentialRetrievalError,
)


class BedrockConfigurationError(ValueError):
    """Bedrock settings are missing or invalid. No AWS request was attempted."""


class BedrockAIUnavailable(RemoteAIUnavailable):
    """A Bedrock request cannot safely produce a trading input."""


class _RetryableBedrockError(BedrockAIUnavailable):
    """A transient Bedrock failure that may succeed if tried again within bounds."""


def _default_client_factory(region: str | None, config: Config) -> BedrockClient:
    """Create a Bedrock Runtime client with standard AWS credential resolution."""
    return boto3.Session(region_name=region).client("bedrock-runtime", config=config)


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    omitted = len(value) - limit
    marker = f"\n[... truncated {omitted} characters to enforce BEDROCK_MAX_INPUT_TOKENS]"
    if limit <= len(marker):
        return marker, True
    return f"{value[: limit - len(marker)]}{marker}", True


def _estimate_tokens(text: str) -> int:
    """Conservative local token estimate used only for budget checks."""
    return max(1, -(-len(text) // _CHARS_PER_TOKEN))


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _usd(tokens: int | None, rate_per_million: float | None) -> float | None:
    if tokens is None or rate_per_million is None:
        return None
    return round(tokens * rate_per_million / 1_000_000, 6)


class BedrockAIProvider(AIProvider):
    """Structured inference through Bedrock Runtime, without local model weights."""

    def __init__(
        self,
        *,
        model_id: str,
        region: str | None,
        max_input_tokens: int,
        max_tokens: int,
        temperature: float,
        timeout_seconds: int,
        max_concurrent_requests: int,
        max_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        max_estimated_usd_per_request: float | None = None,
        input_usd_per_million_tokens: float | None = None,
        output_usd_per_million_tokens: float | None = None,
        health: AIHealth | None = None,
        client_factory: BedrockClientFactory | None = None,
        sleep: SleepFunction | None = None,
    ) -> None:
        if not model_id.strip():
            raise BedrockConfigurationError("BEDROCK_MODEL_ID must be configured")
        if max_input_tokens <= 0:
            raise BedrockConfigurationError("BEDROCK_MAX_INPUT_TOKENS must be positive")
        if max_tokens <= 0:
            raise BedrockConfigurationError("BEDROCK_MAX_TOKENS must be positive")
        if not 0 <= temperature <= 1:
            raise BedrockConfigurationError("BEDROCK_TEMPERATURE must be between 0 and 1")
        if timeout_seconds <= 0:
            raise BedrockConfigurationError("BEDROCK_TIMEOUT_SECONDS must be positive")
        if max_concurrent_requests <= 0:
            raise BedrockConfigurationError("AI_MAX_CONCURRENT_REQUESTS must be positive")
        if max_retries < 0:
            raise BedrockConfigurationError("BEDROCK_MAX_RETRIES cannot be negative")
        if retry_base_seconds <= 0 or retry_max_seconds <= 0:
            raise BedrockConfigurationError("Bedrock retry delays must be positive")
        for name, rate in (
            ("BEDROCK_MAX_ESTIMATED_USD_PER_REQUEST", max_estimated_usd_per_request),
            ("BEDROCK_INPUT_USD_PER_MILLION_TOKENS", input_usd_per_million_tokens),
            ("BEDROCK_OUTPUT_USD_PER_MILLION_TOKENS", output_usd_per_million_tokens),
        ):
            if rate is not None and rate < 0:
                raise BedrockConfigurationError(f"{name} cannot be negative")

        self.model_id = model_id.strip()
        self.region = region.strip() if region and region.strip() else None
        self.max_input_tokens = max_input_tokens
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.max_estimated_usd_per_request = max_estimated_usd_per_request
        self.input_usd_per_million_tokens = input_usd_per_million_tokens
        self.output_usd_per_million_tokens = output_usd_per_million_tokens
        self.health = health or AIHealth()
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent_requests))
        self._client_factory = client_factory or _default_client_factory
        self._sleep = sleep or asyncio.sleep
        self._client: BedrockClient | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        health: AIHealth | None = None,
        model_id: str | None = None,
        input_usd_per_million_tokens: float | None = None,
        output_usd_per_million_tokens: float | None = None,
    ) -> "BedrockAIProvider":
        """Construct a provider from validated settings without contacting AWS.

        Optional overrides select a different model (and its rates) for a
        role, e.g. a cheap model for cadence reviews. ``None`` falls back to
        the main Bedrock settings.
        """
        configured_region = settings.bedrock_region.strip()
        fallback_region = settings.aws_region.strip() if settings.aws_region else ""
        effective_model = model_id if model_id is not None else settings.bedrock_model_id
        if model_id is None:
            effective_input_rate = settings.bedrock_input_usd_per_million_tokens
            effective_output_rate = settings.bedrock_output_usd_per_million_tokens
        else:
            effective_input_rate = input_usd_per_million_tokens
            effective_output_rate = output_usd_per_million_tokens
        return cls(
            model_id=effective_model,
            region=configured_region or fallback_region or None,
            max_input_tokens=settings.bedrock_max_input_tokens,
            max_tokens=settings.bedrock_max_tokens,
            temperature=settings.bedrock_temperature,
            timeout_seconds=settings.bedrock_timeout_seconds,
            max_concurrent_requests=settings.ai_max_concurrent_requests,
            max_retries=settings.bedrock_max_retries,
            retry_base_seconds=settings.bedrock_retry_base_seconds,
            retry_max_seconds=settings.bedrock_retry_max_seconds,
            max_estimated_usd_per_request=settings.bedrock_max_estimated_usd_per_request,
            input_usd_per_million_tokens=effective_input_rate,
            output_usd_per_million_tokens=effective_output_rate,
            health=health,
        )

    @property
    def model_name(self) -> str:
        return self.model_id

    @property
    def provider_label(self) -> str:
        return "bedrock"

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]:
        system_text, system_truncated = _truncate_text(
            system_prompt, self._prompt_budget(system_prompt, user_prompt, which="system")
        )
        user_text, user_truncated = _truncate_text(
            user_prompt, self._prompt_budget(system_prompt, user_prompt, which="user")
        )
        request = self._converse_request(
            response_model, system_text=system_text, user_text=user_text
        )
        self._enforce_cost_budget(system_text, user_text)

        request_started = datetime.now(UTC)
        start = perf_counter()
        async with self._semaphore:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await asyncio.to_thread(self._invoke, request)
                except asyncio.CancelledError:
                    raise
                except _RetryableBedrockError as exc:
                    self.health.record_failure(type(exc).__name__)
                    if attempt >= self.max_retries:
                        logger.warning(
                            "bedrock request failed after retries",
                            extra={"model_id": self.model_id, "attempts": attempt + 1},
                        )
                        raise
                    delay = self._retry_delay(attempt)
                    logger.warning(
                        "bedrock request failed transiently; retrying",
                        extra={
                            "model_id": self.model_id,
                            "attempt": attempt + 1,
                            "delay_seconds": round(delay, 3),
                        },
                    )
                    await self._sleep(delay)
                    continue
                except BedrockAIUnavailable as exc:
                    self.health.record_failure(type(exc).__name__)
                    logger.warning(
                        "bedrock request failed",
                        extra={"model_id": self.model_id, "error": type(exc).__name__},
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 - never let an AI fault escape typed handling
                    self.health.record_failure(type(exc).__name__)
                    raise BedrockAIUnavailable("Bedrock request failed unexpectedly") from exc

                # Local Pydantic validation intentionally happens outside transport handling so
                # the shared schema-retry wrapper can distinguish malformed output.
                try:
                    parsed = response_model.model_validate(self._tool_input(response))
                except ValidationError as exc:
                    # Carry the field-level reason: the retry prompt and the
                    # decision record both need to say WHAT was wrong, otherwise
                    # the model repeats the same mistake and the operator is blind.
                    detail = summarize_validation_errors(exc)
                    self.health.record_failure("AI response failed schema validation")
                    logger.warning(
                        "bedrock response failed schema validation",
                        extra={
                            "model_id": self.model_id,
                            "outcome": "schema_invalid",
                            "schema_detail": detail,
                        },
                    )
                    raise SchemaValidationError(detail) from exc
                except Exception as exc:  # noqa: BLE001 - record every failed interpretation
                    self.health.record_failure(type(exc).__name__)
                    raise
                self.health.record_success()
                completed = datetime.now(UTC)
                usage = response.get("usage", {}) if isinstance(response, dict) else {}
                input_tokens = _optional_int(usage.get("inputTokens"))
                output_tokens = _optional_int(usage.get("outputTokens"))
                estimated_input_usd = _usd(input_tokens, self.input_usd_per_million_tokens)
                estimated_output_usd = _usd(output_tokens, self.output_usd_per_million_tokens)
                estimated_total_usd = (
                    round(estimated_input_usd + estimated_output_usd, 6)
                    if estimated_input_usd is not None and estimated_output_usd is not None
                    else None
                )
                logger.info(
                    "bedrock request completed",
                    extra={
                        "model_id": self.model_id,
                        "region": self._effective_region(),
                        "outcome": "success",
                        "latency_ms": round((perf_counter() - start) * 1000),
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "estimated_total_usd": estimated_total_usd,
                        "transport_retry_count": attempt,
                    },
                )
                return parsed, ProviderMetadata(
                    provider="bedrock",
                    model=self.model_id,
                    request_id=self._request_id(response),
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                    latency_ms=round((perf_counter() - start) * 1000),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    temperature=self.temperature,
                    provider_metadata={
                        "region": self._effective_region(),
                        "request_started_at": request_started.isoformat(),
                        "response_received_at": completed.isoformat(),
                        "schema_validation": "passed",
                        "transport_retry_count": attempt,
                        "retry_count": attempt,
                        "max_output_tokens": self.max_tokens,
                        "max_input_tokens": self.max_input_tokens,
                        "prompt_truncated": system_truncated or user_truncated,
                        "estimated_input_usd": estimated_input_usd,
                        "estimated_output_usd": estimated_output_usd,
                        "estimated_total_usd": estimated_total_usd,
                    },
                )
        raise AssertionError("unreachable")

    def _prompt_budget(self, system_prompt: str, user_prompt: str, *, which: str) -> int:
        """Split the approximate input budget while preserving short prompts exactly."""
        budget = max(1024, self.max_input_tokens * _CHARS_PER_TOKEN)
        system_budget = min(len(system_prompt), max(1024, budget // 8))
        if which == "system":
            return system_budget
        return max(1024, budget - system_budget)

    def _converse_request(
        self, response_model: type[BaseModel], *, system_text: str, user_text: str
    ) -> dict[str, Any]:
        tool_schema = dict(response_model.model_json_schema())
        if tool_schema.get("type") != "object":
            raise BedrockConfigurationError(
                "Bedrock structured responses require an object-shaped response model"
            )
        # The shared prompt files tell the model to "return JSON", which is written
        # for the OpenAI-compatible json_object path. On Bedrock the response must
        # arrive as a tool call, so restate the transport explicitly: some models
        # otherwise obey the prompt text and reply with raw JSON (stopReason
        # end_turn), which this provider must reject as unstructured.
        transport_note = (
            " Transport: you MUST return your response by calling the "
            f"`{_TOOL_NAME}` tool with the complete response as its input. "
            "Do not reply with plain text or raw JSON."
        )
        return {
            "modelId": self.model_id,
            "system": [{"text": system_text + transport_note}],
            "messages": [{"role": "user", "content": [{"text": user_text}]}],
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": self.temperature},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": _TOOL_NAME,
                            "description": "Return the requested response as structured tool input.",
                            "inputSchema": {"json": tool_schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": _TOOL_NAME}},
            },
        }

    def _enforce_cost_budget(self, system_text: str, user_text: str) -> None:
        """Refuse before inference when the worst-case configured cost is exceeded."""
        if self.max_estimated_usd_per_request is None:
            return
        if self.input_usd_per_million_tokens is None or self.output_usd_per_million_tokens is None:
            return
        estimated_input_usd = _usd(
            _estimate_tokens(system_text + user_text), self.input_usd_per_million_tokens
        )
        estimated_output_usd = _usd(self.max_tokens, self.output_usd_per_million_tokens)
        if estimated_input_usd is None or estimated_output_usd is None:
            return
        if estimated_input_usd + estimated_output_usd > self.max_estimated_usd_per_request:
            raise BedrockAIUnavailable(
                "Projected Bedrock request cost exceeds BEDROCK_MAX_ESTIMATED_USD_PER_REQUEST"
            )

    def _invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        client = self._client_factory(self.region, self._client_config())
        self._client = client
        try:
            response = client.converse(**request)
        except ClientError as exc:
            raise self._classify_client_error(exc) from exc
        except (ConnectTimeoutError, ReadTimeoutError) as exc:
            raise _RetryableBedrockError("Bedrock request timed out") from exc
        except (EndpointConnectionError, ConnectionClosedError) as exc:
            raise _RetryableBedrockError(
                "Bedrock request failed before receiving a response"
            ) from exc
        except _CREDENTIAL_ERRORS as exc:
            raise BedrockAIUnavailable(
                "AWS credentials are unavailable; configure the standard AWS credential chain"
            ) from exc
        except (NoRegionError, InvalidRegionError, UnknownRegionError) as exc:
            raise BedrockAIUnavailable(
                "AWS Bedrock region is unavailable; set BEDROCK_REGION or AWS_REGION"
            ) from exc
        except (ParamValidationError, SSLError, BotoCoreError) as exc:
            raise BedrockAIUnavailable("Bedrock rejected the constructed request") from exc
        if not isinstance(response, dict):
            raise BedrockAIUnavailable("Bedrock returned a malformed response envelope")
        return response

    def _client_config(self) -> Config:
        return Config(
            connect_timeout=min(10, self.timeout_seconds),
            read_timeout=self.timeout_seconds,
            retries={"max_attempts": 1, "mode": "standard"},
        )

    def _classify_client_error(self, exc: ClientError) -> BedrockAIUnavailable:
        response = exc.response if isinstance(exc.response, dict) else {}
        error = response.get("Error", {})
        code = str(error.get("Code", "") or "")
        metadata = response.get("ResponseMetadata", {})
        status = metadata.get("HTTPStatusCode")
        if code in _RETRYABLE_ERROR_CODES or status in _RETRYABLE_HTTP_STATUSES:
            return _RetryableBedrockError("Bedrock request failed transiently")
        if code in {
            "UnrecognizedClientException",
            "InvalidSignatureException",
            "ExpiredToken",
            "ExpiredTokenException",
            "InvalidToken",
            "MissingAuthenticationToken",
        }:
            return BedrockAIUnavailable("Bedrock rejected the AWS credentials")
        if code in {
            "AccessDenied",
            "AccessDeniedException",
            "Unauthorized",
            "UnauthorizedException",
            "Forbidden",
            "ForbiddenException",
        }:
            return BedrockAIUnavailable("Bedrock denied access to the configured model")
        if code in {
            "ResourceNotFoundException",
            "ValidationException",
            "ValidationError",
            "ModelError",
            "ModelNotReadyException",
        }:
            return BedrockAIUnavailable("Bedrock rejected the model identifier or request")
        return BedrockAIUnavailable("Bedrock request failed")

    @staticmethod
    def _tool_input(response: dict[str, Any]) -> Any:
        if response.get("stopReason") != "tool_use":
            raise BedrockAIUnavailable("Bedrock stopped before returning structured output")
        output = response.get("output")
        if not isinstance(output, dict):
            raise BedrockAIUnavailable("Bedrock returned a malformed response envelope")
        message = output.get("message")
        if not isinstance(message, dict):
            raise BedrockAIUnavailable("Bedrock returned a malformed response envelope")
        content = message.get("content")
        if not isinstance(content, list):
            raise BedrockAIUnavailable("Bedrock returned a malformed response envelope")
        tool_uses = [
            block["toolUse"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("toolUse"), dict)
        ]
        if len(tool_uses) != 1:
            raise BedrockAIUnavailable("Bedrock did not return exactly one structured result")
        tool_use = tool_uses[0]
        if tool_use.get("name") != _TOOL_NAME or "input" not in tool_use:
            raise BedrockAIUnavailable("Bedrock returned an unexpected structured result")
        structured = tool_use["input"]
        if (
            isinstance(structured, dict)
            and set(structured.keys()) == {"input"}
            and isinstance(structured["input"], dict)
        ):
            # Some models wrap the tool payload in an extra {"input": {...}}
            # envelope. Unwrap it; a genuine decision object never has a
            # top-level "input" key, so this cannot mask a real schema error.
            structured = structured["input"]
        return structured

    @staticmethod
    def _request_id(response: dict[str, Any]) -> str | None:
        metadata = response.get("ResponseMetadata")
        if not isinstance(metadata, dict):
            return None
        request_id = metadata.get("RequestId")
        return request_id if isinstance(request_id, str) else None

    def _effective_region(self) -> str:
        if self.region:
            return self.region
        client = self._client
        metadata = getattr(client, "meta", None)
        region = getattr(metadata, "region_name", None)
        return region if isinstance(region, str) and region else "unknown"

    def _retry_delay(self, attempt: int) -> float:
        delay = self.retry_base_seconds * (2**attempt)
        return float(min(self.retry_max_seconds, delay * random.uniform(0.5, 1.5)))
