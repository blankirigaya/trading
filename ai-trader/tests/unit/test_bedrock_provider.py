"""Unit and wiring tests for the Amazon Bedrock AI provider.

All AWS interactions are simulated with an injected fake client. These tests verify the
provider contract, retries, failure handling, configuration, cost controls, response
validation, and Bedrock selection. No AWS credentials are required and no inference
cost is incurred.
"""

import hashlib
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError
from pydantic import BaseModel
from sqlalchemy import create_engine

import ai_trader.integrations.connectivity as connectivity
from ai_trader.ai.base import AIProvider
from ai_trader.ai.bedrock import (
    BedrockAIProvider,
    BedrockAIUnavailable,
    BedrockConfigurationError,
)
from ai_trader.ai.errors import SchemaValidationError
from ai_trader.ai.providers import build_ai_provider
from ai_trader.ai.remote_model import AIHealth, OpenAICompatibleAIProvider
from ai_trader.config import Settings
from ai_trader.domain.decisions import CriticAssessment, PortfolioDecision
from ai_trader.domain.news import (
    AnalysisStatus,
    NewsCategory,
    NewsItem,
    SourceType,
    TrustLevel,
)
from ai_trader.news.analyst import NewsAnalysisResponse
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


class EchoResponse(BaseModel):
    answer: str


class FakeBedrockClient:
    def __init__(self, outcomes):
        self.requests = []
        self._outcomes = list(outcomes)
        self.closed = False

    def converse(self, **request):
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def _valid_news_payload():
    return {
        "event_type": "ORDER_WIN",
        "summary": "Infosys secured a large multi-year contract.",
        "affected_symbols": ["INFY"],
        "materiality": "HIGH",
        "direction": "POSITIVE",
        "horizon": "WEEKS",
        "confidence": 0.74,
        "requires_portfolio_review": False,
        "evidence": ["Company disclosure of contract value and duration."],
        "uncertainties": ["Revenue recognition schedule was not disclosed."],
    }


def _tool_response(payload, usage=None, request_id="bedrock-request"):
    return {
        "ResponseMetadata": {"RequestId": request_id, "HTTPStatusCode": 200},
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tool-1",
                            "name": "return_structured_decision",
                            "input": payload,
                        }
                    }
                ],
            }
        },
        "usage": usage or {"inputTokens": 30, "outputTokens": 20, "totalTokens": 50},
    }


def _client_error(code, status=400):
    return ClientError(
        {
            "Error": {"Code": code, "Message": "simulated AWS error"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


def _provider(fake, **overrides):
    arguments = {
        "model_id": "apac.amazon.nova-pro-v1:0",
        "region": "ap-south-1",
        "max_input_tokens": 32000,
        "max_tokens": 1024,
        "temperature": 0.0,
        "timeout_seconds": 30,
        "max_concurrent_requests": 1,
        "max_retries": 2,
        "retry_base_seconds": 0.5,
        "retry_max_seconds": 8.0,
        "health": AIHealth(),
        "client_factory": lambda region, config: fake,
    }
    arguments.update(overrides)
    return BedrockAIProvider(**arguments)


def _news_item():
    return NewsItem(
        item_id="bedrock-item-1",
        source_id="nse-corporate",
        source_name="NSE India",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
        headline="Infosys wins a five thousand crore order",
        canonical_url="https://example.invalid/bedrock-item-1",
        url="https://example.invalid/bedrock-item-1",
        published_at=NOW,
        received_at=NOW,
        category=NewsCategory.CORPORATE_ANNOUNCEMENT,
        symbols=("INFY",),
        content_hash=hashlib.sha256(b"bedrock-item-1").hexdigest(),
        headline_hash=hashlib.sha256(b"bedrock headline").hexdigest(),
        excerpt="The company disclosed the contract in a filing.",
    )


def _valid_portfolio_payload():
    return {
        "cash_percent": 100,
        "allocations": [],
        "proposals": [],
        "market_summary": "Market context.",
        "decision_summary": "Stay in cash.",
    }


def _valid_critic_payload():
    return {
        "score": 0.8,
        "agrees": True,
        "disagreement_signals": [],
        "critique": "The proposal is consistent with the supplied evidence.",
        "bounded_decisions": ["APPROVE"],
    }


async def _never_sleep(delay):
    raise AssertionError("transient-error retries should not have been attempted")


async def test_bedrock_success_returns_validated_response_with_provenance():
    fake = FakeBedrockClient([_tool_response(_valid_news_payload())])
    provider = _provider(
        fake,
        input_usd_per_million_tokens=3.0,
        output_usd_per_million_tokens=15.0,
    )

    response, metadata = await provider.structured_completion(
        "system prompt",
        "user prompt",
        NewsAnalysisResponse,
        prompt_name="news_analyst",
        prompt_version="v1",
    )

    assert isinstance(response, NewsAnalysisResponse)
    assert response.event_type == "ORDER_WIN"
    assert metadata.provider == "bedrock"
    assert metadata.model == "apac.amazon.nova-pro-v1:0"
    assert metadata.request_id == "bedrock-request"
    assert metadata.input_tokens == 30
    assert metadata.output_tokens == 20
    assert metadata.temperature == 0.0
    assert metadata.provider_metadata["region"] == "ap-south-1"
    assert metadata.provider_metadata["schema_validation"] == "passed"
    assert metadata.provider_metadata["transport_retry_count"] == 0
    assert metadata.provider_metadata["estimated_input_usd"] == pytest.approx(0.00009)
    assert metadata.provider_metadata["estimated_output_usd"] == pytest.approx(0.0003)
    assert metadata.provider_metadata["estimated_total_usd"] == pytest.approx(0.00039)
    assert provider.health.reachable is True
    await provider.aclose()
    assert fake.closed is True


@pytest.mark.parametrize(
    ("response_model", "payload"),
    [
        (NewsAnalysisResponse, _valid_news_payload()),
        (PortfolioDecision, _valid_portfolio_payload()),
        (CriticAssessment, _valid_critic_payload()),
    ],
)
async def test_bedrock_forwards_native_response_schema_without_rewriting(response_model, payload):
    fake = FakeBedrockClient([_tool_response(payload)])
    provider = _provider(fake)

    parsed, _ = await provider.structured_completion(
        "system prompt",
        "user prompt",
        response_model,
        prompt_name="contract",
        prompt_version="v1",
    )

    assert isinstance(parsed, response_model)
    request = fake.requests[0]
    assert request["modelId"] == "apac.amazon.nova-pro-v1:0"
    (system_block,) = request["system"]
    assert system_block["text"].startswith("system prompt")
    assert "return_structured_decision" in system_block["text"]
    assert request["messages"] == [{"role": "user", "content": [{"text": "user prompt"}]}]
    assert request["inferenceConfig"] == {"maxTokens": 1024, "temperature": 0.0}
    tool = request["toolConfig"]["tools"][0]["toolSpec"]
    assert tool["name"] == "return_structured_decision"
    assert tool["inputSchema"]["json"] == response_model.model_json_schema()
    assert request["toolConfig"]["toolChoice"] == {"tool": {"name": "return_structured_decision"}}


@pytest.mark.parametrize(
    "response",
    [
        {
            "stopReason": "max_tokens",
            "output": {"message": {"role": "assistant", "content": [{"text": "cut off"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 10},
        },
        {
            "stopReason": "tool_use",
            "output": {"message": {"role": "assistant", "content": [{"text": "no tool"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 10},
        },
        {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-1",
                                "name": "wrong_tool",
                                "input": {"answer": "x"},
                            }
                        }
                    ],
                }
            },
            "usage": {"inputTokens": 10, "outputTokens": 10},
        },
    ],
)
async def test_malformed_bedrock_responses_fail_closed_without_schema_retry(response):
    fake = FakeBedrockClient([response])
    provider = _provider(fake, sleep=_never_sleep)

    with pytest.raises(BedrockAIUnavailable):
        await provider.structured_completion(
            "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
        )

    assert len(fake.requests) == 1
    # One malformed response fails closed but does not flip the badge on its
    # own (stays UNKNOWN on a fresh object); the error is recorded.
    assert provider.health.reachable is None
    assert provider.health.last_error is not None


async def test_out_of_range_tool_result_is_rejected_by_local_schema_validation():
    payload = _valid_news_payload() | {"confidence": 2.0}
    fake = FakeBedrockClient([_tool_response(payload)])
    provider = _provider(fake)

    with pytest.raises(SchemaValidationError) as raised:
        await provider.structured_completion(
            "system prompt",
            "user prompt",
            NewsAnalysisResponse,
            prompt_name="news_analyst",
            prompt_version="v1",
        )

    # The failure must name the offending field, not just say "invalid".
    assert "confidence" in raised.value.detail
    assert "confidence" in str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        _client_error("AccessDeniedException", status=400),
        _client_error("ResourceNotFoundException", status=400),
        _client_error("ValidationException", status=400),
        NoCredentialsError(),
    ],
)
async def test_authentication_permission_and_request_errors_are_not_retried(error):
    fake = FakeBedrockClient([error])
    provider = _provider(fake, max_retries=2, sleep=_never_sleep)

    with pytest.raises(BedrockAIUnavailable):
        await provider.structured_completion(
            "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
        )

    assert len(fake.requests) == 1


async def test_throttling_is_retried_with_bounded_backoff_and_jitter(monkeypatch):
    fake = FakeBedrockClient(
        [
            _client_error("ThrottlingException", status=400),
            _client_error("ThrottlingException", status=400),
            _tool_response({"answer": "recovered"}),
        ]
    )
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("ai_trader.ai.bedrock.random.uniform", lambda low, high: 1.0)
    provider = _provider(fake, max_retries=2, sleep=record_sleep)

    response, metadata = await provider.structured_completion(
        "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
    )

    assert response.answer == "recovered"
    assert len(fake.requests) == 3
    assert delays == [0.5, 1.0]
    assert metadata.provider_metadata["transport_retry_count"] == 2


async def test_exhausted_retries_fail_closed():
    fake = FakeBedrockClient([_client_error("ThrottlingException", status=400)] * 3)
    provider = _provider(fake, max_retries=2, sleep=lambda delay: _null_sleep())

    with pytest.raises(BedrockAIUnavailable):
        await provider.structured_completion(
            "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
        )

    assert len(fake.requests) == 3


async def _null_sleep():
    return None


async def test_timeout_uses_the_configured_boto_timeout():
    fake = FakeBedrockClient(
        [ReadTimeoutError(endpoint_url="https://example.invalid", error="timed out")]
    )
    seen = {}

    def factory(region, config):
        seen["region"] = region
        seen["read_timeout"] = config.read_timeout
        seen["connect_timeout"] = config.connect_timeout
        return fake

    provider = _provider(fake, client_factory=factory, max_retries=0, sleep=_never_sleep)

    with pytest.raises(BedrockAIUnavailable, match="timed out"):
        await provider.structured_completion(
            "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
        )

    assert seen == {"region": "ap-south-1", "read_timeout": 30, "connect_timeout": 10}


async def test_input_is_truncated_within_the_configured_token_budget():
    fake = FakeBedrockClient([_tool_response({"answer": "ok"})])
    provider = _provider(fake, max_input_tokens=10)

    await provider.structured_completion(
        "s" * 5000, "u" * 5000, EchoResponse, prompt_name="x", prompt_version="v1"
    )

    request = fake.requests[0]
    assert "BEDROCK_MAX_INPUT_TOKENS" in request["system"][0]["text"]
    assert "BEDROCK_MAX_INPUT_TOKENS" in request["messages"][0]["content"][0]["text"]


async def test_request_cost_budget_is_enforced_before_any_aws_call():
    fake = FakeBedrockClient([_tool_response({"answer": "unused"})])
    provider = _provider(
        fake,
        max_estimated_usd_per_request=0.000001,
        input_usd_per_million_tokens=100.0,
        output_usd_per_million_tokens=100.0,
    )

    with pytest.raises(BedrockAIUnavailable, match="Projected Bedrock request cost"):
        await provider.structured_completion(
            "system prompt", "user prompt", EchoResponse, prompt_name="x", prompt_version="v1"
        )

    assert fake.requests == []


def test_missing_model_id_is_a_configuration_error_not_an_aws_error():
    with pytest.raises(BedrockConfigurationError, match="BEDROCK_MODEL_ID"):
        BedrockAIProvider.from_settings(
            Settings(ai_provider="bedrock", bedrock_model_id="  ", bedrock_region="ap-south-1")
        )


def test_invalid_bedrock_settings_are_rejected():
    with pytest.raises(ValueError, match="BEDROCK_TEMPERATURE"):
        Settings(ai_provider="bedrock", bedrock_temperature=2.0)
    with pytest.raises(ValueError, match="BEDROCK_MAX_RETRIES"):
        Settings(ai_provider="bedrock", bedrock_max_retries=-1)
    with pytest.raises(ValueError, match="Bedrock cost controls"):
        Settings(ai_provider="bedrock", bedrock_input_usd_per_million_tokens=-1.0)


def test_default_bedrock_region_model_and_safety_flags():
    settings = Settings(ai_provider="bedrock")

    assert settings.bedrock_region == "ap-south-1"
    assert settings.bedrock_model_id == "apac.amazon.nova-pro-v1:0"
    assert settings.live_order_execution is False
    assert settings.trading_enabled is False


def test_explicit_bedrock_region_takes_precedence_over_aws_region():
    provider = BedrockAIProvider.from_settings(
        Settings(ai_provider="bedrock", bedrock_region="ap-south-1", aws_region="us-east-1")
    )

    assert provider.region == "ap-south-1"


def test_aws_region_is_used_when_bedrock_region_is_blank():
    provider = BedrockAIProvider.from_settings(
        Settings(ai_provider="bedrock", bedrock_region="", aws_region="ap-south-1")
    )

    assert provider.region == "ap-south-1"


def test_provider_factory_selects_configured_backend():
    remote = build_ai_provider(
        Settings(
            ai_provider="remote_openai_compatible",
            ai_base_url="http://ai-node:8080/v1",
            ai_model="server-model",
        )
    )
    bedrock = build_ai_provider(Settings(ai_provider="bedrock"))

    assert isinstance(remote, OpenAICompatibleAIProvider)
    assert isinstance(bedrock, BedrockAIProvider)
    assert isinstance(bedrock, AIProvider)


def test_provider_factory_rejects_unknown_backends():
    with pytest.raises(ValueError, match="Unsupported AI_PROVIDER"):
        build_ai_provider(Settings(ai_provider="mystery"))


async def test_bedrock_connectivity_probe_reports_model_region_and_usage(monkeypatch):
    fake = FakeBedrockClient([_tool_response(_valid_portfolio_payload())])
    provider = _provider(fake)
    monkeypatch.setattr(connectivity, "build_ai_provider", lambda settings, health=None: provider)

    result = await connectivity.probe_ai(Settings(ai_provider="bedrock"))

    assert result.ok is True
    assert "provider: bedrock" in result.lines
    assert "model: apac.amazon.nova-pro-v1:0" in result.lines
    assert "region: ap-south-1" in result.lines
    assert "structured_schema_valid: yes" in result.lines
    assert len(fake.requests) == 1


def test_bedrock_module_contains_no_execution_paths():
    from pathlib import Path

    text = Path("src/ai_trader/ai/bedrock.py").read_text(encoding="utf-8")
    for token in ("PaperBroker", "place_order", "ExecutionService", "OrderRequest", "Broker"):
        assert token not in text


async def test_bedrock_news_failure_is_recorded_without_a_portfolio_review():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = NewsRepository(engine)
    settings = Settings(ai_provider="bedrock")
    fake = FakeBedrockClient([_client_error("AccessDeniedException", status=400)])
    provider = _provider(fake)
    intelligence = NewsIntelligence(settings, repository, provider)
    item = _news_item()
    intelligence.ingest_items([item])

    analysis = await intelligence.analyse_item(intelligence.items[item.item_id])

    assert analysis.status is AnalysisStatus.FAILED
    assert analysis.requires_portfolio_review is False
    assert "denied access" in (analysis.failure_reason or "").lower()
    assert repository.get_analysis(analysis.analysis_id) is not None


async def test_completed_bedrock_news_analysis_is_reused_instead_of_rebilled():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = NewsRepository(engine)
    settings = Settings(ai_provider="bedrock")
    fake = FakeBedrockClient([_tool_response(_valid_news_payload())])
    provider = _provider(fake)
    intelligence = NewsIntelligence(settings, repository, provider)
    intelligence.ingest_items([_news_item()])

    first = await intelligence.analyse_latest(limit=5)
    second = await intelligence.analyse_latest(limit=5)

    assert len(first) == 1
    assert second == []
    assert len(fake.requests) == 1


def test_cadence_flag_selects_the_cheap_model_with_its_rates():
    provider = build_ai_provider(
        Settings(
            ai_provider="bedrock",
            bedrock_model_id="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
            bedrock_input_usd_per_million_tokens=3.0,
            bedrock_output_usd_per_million_tokens=15.0,
            bedrock_cadence_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_cadence_input_usd_per_million_tokens=0.035,
            bedrock_cadence_output_usd_per_million_tokens=0.14,
        ),
        cadence=True,
    )

    assert isinstance(provider, BedrockAIProvider)
    assert provider.model_id == "apac.amazon.nova-micro-v1:0"
    assert provider.input_usd_per_million_tokens == 0.035
    assert provider.output_usd_per_million_tokens == 0.14


def test_cadence_without_a_cheap_model_falls_back_to_main():
    settings = Settings(
        ai_provider="bedrock",
        bedrock_model_id="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        bedrock_input_usd_per_million_tokens=3.0,
        bedrock_output_usd_per_million_tokens=15.0,
        bedrock_cadence_model_id="",
        bedrock_cadence_input_usd_per_million_tokens=None,
        bedrock_cadence_output_usd_per_million_tokens=None,
    )
    provider = build_ai_provider(settings, cadence=True)

    assert isinstance(provider, BedrockAIProvider)
    assert provider.model_id == "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    assert provider.input_usd_per_million_tokens == 3.0
    assert provider.output_usd_per_million_tokens == 15.0


def test_cadence_rates_fall_back_to_main_rates_when_unset():
    provider = build_ai_provider(
        Settings(
            ai_provider="bedrock",
            bedrock_model_id="main-model",
            bedrock_input_usd_per_million_tokens=3.0,
            bedrock_output_usd_per_million_tokens=15.0,
            bedrock_cadence_model_id="cheap-model",
            bedrock_cadence_input_usd_per_million_tokens=None,
            bedrock_cadence_output_usd_per_million_tokens=None,
        ),
        cadence=True,
    )

    assert isinstance(provider, BedrockAIProvider)
    assert provider.model_id == "cheap-model"
    assert provider.input_usd_per_million_tokens == 3.0
    assert provider.output_usd_per_million_tokens == 15.0


def test_main_model_is_untouched_by_the_cadence_flag():
    provider = build_ai_provider(
        Settings(
            ai_provider="bedrock",
            bedrock_model_id="main-model",
            bedrock_cadence_model_id="cheap-model",
        )
    )

    assert isinstance(provider, BedrockAIProvider)
    assert provider.model_id == "main-model"
