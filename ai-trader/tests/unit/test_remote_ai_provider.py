import httpx
import pytest

from ai_trader.ai.errors import SchemaValidationError
from ai_trader.ai.remote_model import (
    DEGRADED_AFTER_CONSECUTIVE_FAILURES,
    AIHealth,
    OpenAICompatibleAIProvider,
    RemoteAIUnavailable,
    sanitize_server_identifier,
)
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioDecision

CREDENTIAL_URL = "http://trader:hunter2@192.168.1.25:8080/v1/chat?api_key=abcd1234"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://192.168.1.25:8080/v1", "192.168.1.25:8080"),
        ("https://ai-node.internal:8080/v1/chat/completions", "ai-node.internal:8080"),
        ("http://ai-node.internal/v1", "ai-node.internal"),
        ("https://ai-node.internal", "ai-node.internal"),
        ("ai-node.internal:11434", "ai-node.internal:11434"),
    ],
)
def test_sanitized_identifier_is_host_and_port_only(raw: str, expected: str) -> None:
    assert sanitize_server_identifier(raw) == expected


def test_credentials_never_appear_in_persisted_provenance() -> None:
    """Deliberately credential-bearing URL must not leak into stored provenance."""
    provider = OpenAICompatibleAIProvider(
        CREDENTIAL_URL, "server-model-id", "top-secret-key", 120, 1
    )
    identifier = provider.server_identifier

    assert identifier == "192.168.1.25:8080"
    for secret in ("trader", "hunter2", "abcd1234", "top-secret-key", "http://", "/v1", "api_key"):
        assert secret not in identifier


def test_health_is_unchanged_until_traffic_occurs() -> None:
    health = AIHealth()
    assert health.snapshot() == {
        "ai_server_reachable": None,
        "last_ai_success_at": None,
        "last_ai_failure_at": None,
        "last_ai_error": None,
    }


def test_health_records_success_and_failure() -> None:
    health = AIHealth()
    health.record_success()
    assert health.reachable is True
    assert health.last_success_at is not None
    # A lone transient failure is dampened: the badge must not flap on one
    # red review among hundreds of green ones.
    health.record_failure("boom")
    assert health.reachable is True
    assert health.last_failure_at is not None
    assert health.last_error == "boom"
    # ...but a sustained run with no success in between means genuinely down.
    for _ in range(DEGRADED_AFTER_CONSECUTIVE_FAILURES - 1):
        health.record_failure("boom")
    assert health.reachable is False
    # Any success recovers immediately.
    health.record_success()
    assert health.reachable is True
    assert health.last_error is None


async def test_pooled_client_is_reused_across_requests() -> None:
    provider = OpenAICompatibleAIProvider("http://ai-node:8080/v1", "m", None, 5, 1)
    first = provider._get_client()
    second = provider._get_client()
    assert first is second
    await provider.aclose()
    assert first.is_closed


async def test_client_is_recreated_after_close() -> None:
    provider = OpenAICompatibleAIProvider("http://ai-node:8080/v1", "m", None, 5, 1)
    first = provider._get_client()
    await provider.aclose()
    assert provider._get_client() is not first
    await provider.aclose()


async def test_network_failure_marks_health_and_fails_closed() -> None:
    health = AIHealth()
    provider = OpenAICompatibleAIProvider(
        "http://127.0.0.1:1/v1",
        "m",
        None,
        timeout_seconds=1,
        max_concurrent_requests=1,
        health=health,
    )
    with pytest.raises(RemoteAIUnavailable):
        await provider.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="t", prompt_version="v1"
        )
    # Fail-closed is about raising, not the badge: one refused connection is
    # dampened (a fresh object stays UNKNOWN, never flips straight to down),
    # the error is still recorded for diagnostics.
    assert health.last_error is not None
    assert health.reachable is None
    await provider.aclose()


async def test_http_error_marks_health_failure() -> None:
    health = AIHealth()
    provider = OpenAICompatibleAIProvider("http://ai-node:8080/v1", "m", None, 5, 1, health=health)
    transport = httpx.MockTransport(lambda request: httpx.Response(503, text="unavailable"))
    provider._client = httpx.AsyncClient(transport=transport)
    with pytest.raises(RemoteAIUnavailable):
        await provider.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="t", prompt_version="v1"
        )
    # A single 503 is dampened (stays UNKNOWN on a fresh object, never
    # straight to down); the error is recorded.
    assert "503" in (health.last_error or "")
    assert health.reachable is None
    await provider.aclose()


async def test_successful_completion_records_provenance_without_secrets() -> None:
    health = AIHealth()
    provider = OpenAICompatibleAIProvider(
        CREDENTIAL_URL, "server-model-id", "top-secret-key", 5, 1, health=health
    )
    payload = {
        "id": "chatcmpl-abc",
        "choices": [
            {
                "message": {
                    "content": '{"cash_percent":100,"allocations":[],"proposals":[],"market_summary":"m","decision_summary":"stay in cash"}'
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, headers={"x-request-id": "req-42"})
    )
    provider._client = httpx.AsyncClient(transport=transport)

    decision, metadata = await provider.structured_completion(
        "s", "u", PortfolioDecision, prompt_name="portfolio_manager", prompt_version="v2"
    )

    assert decision.cash_percent == 100
    assert metadata.request_id == "req-42"
    assert metadata.input_tokens == 11
    assert metadata.provider_metadata["server"] == "192.168.1.25:8080"
    assert metadata.provider_metadata["schema_validation"] == "passed"
    serialized = str(metadata.model_dump())
    for secret in ("hunter2", "abcd1234", "top-secret-key"):
        assert secret not in serialized
    assert health.reachable is True
    await provider.aclose()


def test_settings_remain_remote_oriented() -> None:
    settings = Settings(ai_provider="remote_openai_compatible")
    assert settings.ai_provider == "remote_openai_compatible"
    assert settings.ai_base_url == ""
    assert settings.ai_timeout_seconds == 120
    assert settings.ai_max_concurrent_requests == 1
    assert settings.live_order_execution is False


async def test_schema_failure_names_the_missing_field() -> None:
    """A bare "invalid" is undiagnosable; the error must say which field broke."""
    provider = OpenAICompatibleAIProvider("http://ai-node:8080/v1", "m", None, 5, 1)
    # cash_percent is required by PortfolioDecision and deliberately absent.
    content = '{"allocations":[],"proposals":[],"market_summary":"m","decision_summary":"d"}'
    payload = {"choices": [{"message": {"content": content}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    provider._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(SchemaValidationError) as raised:
        await provider.structured_completion(
            "s", "u", PortfolioDecision, prompt_name="t", prompt_version="v1"
        )

    assert "cash_percent" in raised.value.detail
    assert "cash_percent" in str(raised.value)
    await provider.aclose()
