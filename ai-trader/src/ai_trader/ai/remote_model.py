import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter
from typing import TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from ai_trader.ai.base import AIProvider
from ai_trader.ai.errors import SchemaValidationError, summarize_validation_errors
from ai_trader.domain.decisions import ProviderMetadata

T = TypeVar("T", bound=BaseModel)


class RemoteAIUnavailable(RuntimeError):
    """The configured network inference service cannot safely produce a decision."""


def sanitize_server_identifier(base_url: str) -> str:
    """Return only hostname plus optional port.

    Scheme, path, query, fragment, username, and password are never persisted.
    """
    parsed = urlparse(base_url if "://" in base_url else f"//{base_url}")
    host = parsed.hostname
    if not host:
        return "unknown"
    try:
        port = parsed.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port else host


# A lone failed review (and even one that exhausts its transport retries)
# must not flip the AI badge; only a sustained run of failures — with no
# success in between — means the provider is actually down. Sized above
# attempts-per-review (Bedrock max_retries + 1 = 3).
DEGRADED_AFTER_CONSECUTIVE_FAILURES = 6


class AIHealth:
    """Lightweight provider health updated by real traffic, never by status polling."""

    def __init__(self) -> None:
        self.reachable: bool | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0

    def record_success(self) -> None:
        self.reachable = True
        self.consecutive_failures = 0
        self.last_success_at = datetime.now(UTC)
        self.last_error = None

    def record_failure(self, error: str) -> None:
        # Dampened: one review fans out to several attempts (Bedrock retries
        # each failed attempt) across several providers sharing this object,
        # so a single red review must not paint the whole AI stack DEGRADED.
        # Only sustained consecutive failures flip the badge; any success
        # recovers immediately. The threshold exceeds attempts-per-review
        # (Bedrock max_retries + 1 = 3), so an isolated bad review — even one
        # that exhausts its retries — never flips it on its own.
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(UTC)
        self.last_error = error
        if self.consecutive_failures >= DEGRADED_AFTER_CONSECUTIVE_FAILURES:
            self.reachable = False

    def snapshot(self) -> dict[str, object]:
        return {
            "ai_server_reachable": self.reachable,
            "last_ai_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "last_ai_failure_at": self.last_failure_at.isoformat()
            if self.last_failure_at
            else None,
            "last_ai_error": self.last_error,
        }


class OpenAICompatibleAIProvider(AIProvider):
    """Pooled network client for any OpenAI-compatible inference server.

    The pooled httpx client is created lazily and closed explicitly via aclose().
    The owning application container closes it during shutdown.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: int = 120,
        max_concurrent_requests: int = 1,
        health: AIHealth | None = None,
    ) -> None:
        if not base_url or not model:
            raise ValueError("AI_BASE_URL and AI_MODEL must be configured")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_concurrent_requests = max_concurrent_requests
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._client: httpx.AsyncClient | None = None
        self.health = health or AIHealth()

    @property
    def server_identifier(self) -> str:
        return sanitize_server_identifier(self.base_url)

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def provider_label(self) -> str:
        return "remote_openai_compatible"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=self.max_concurrent_requests,
                max_keepalive_connections=self.max_concurrent_requests,
            )
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                limits=limits,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def available_models(self) -> list[str] | None:
        """Model discovery is best-effort; a 404 means the server does not expose the list."""
        try:
            response = await self._get_client().get(
                f"{self.base_url}/models", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            self.health.record_failure(f"model discovery failed: {type(exc).__name__}")
            raise RemoteAIUnavailable("AI model discovery failed") from exc
        if response.status_code == 404:
            self.health.record_success()
            return None
        if response.is_error:
            self.health.record_failure(f"model discovery returned HTTP {response.status_code}")
            raise RemoteAIUnavailable("AI model discovery failed")
        try:
            body = response.json()
            return [str(item["id"]) for item in body.get("data", [])]
        except (ValueError, TypeError, KeyError) as exc:
            self.health.record_failure("model discovery returned malformed JSON")
            raise RemoteAIUnavailable("AI model discovery failed") from exc

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        prompt_name: str,
        prompt_version: str,
    ) -> tuple[T, ProviderMetadata]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        request_started = datetime.now(UTC)
        start = perf_counter()
        try:
            async with self._semaphore:
                response = await self._get_client().post(
                    f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                parsed = response_model.model_validate(json.loads(content))
        except httpx.HTTPStatusError as exc:
            self.health.record_failure(
                f"inference request returned HTTP {exc.response.status_code}"
            )
            raise RemoteAIUnavailable("AI structured completion failed") from exc
        except httpx.HTTPError as exc:
            self.health.record_failure(f"inference request failed: {type(exc).__name__}")
            raise RemoteAIUnavailable("AI structured completion failed") from exc
        except ValidationError as exc:
            detail = summarize_validation_errors(exc)
            self.health.record_failure("AI response failed schema validation")
            raise SchemaValidationError(detail) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.health.record_failure("AI response failed schema validation")
            raise RemoteAIUnavailable("AI structured completion failed") from exc

        self.health.record_success()
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        metadata: dict[str, object] = {
            "server": self.server_identifier,
            "request_started_at": request_started.isoformat(),
            "response_received_at": datetime.now(UTC).isoformat(),
            "schema_validation": "passed",
            "retry_count": 0,
        }
        if isinstance(usage, dict):
            metadata["input_tokens"] = usage.get("prompt_tokens")
            metadata["output_tokens"] = usage.get("completion_tokens")
        return parsed, ProviderMetadata(
            provider="remote_openai_compatible",
            model=self.model,
            request_id=response.headers.get("x-request-id"),
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=round((perf_counter() - start) * 1000),
            input_tokens=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            output_tokens=usage.get("completion_tokens") if isinstance(usage, dict) else None,
            provider_metadata=metadata,
        )

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}
