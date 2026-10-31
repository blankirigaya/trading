"""Developer connectivity probes for the real integration path.

Every probe reports what actually happened and never substitutes mock results.
Credentials are read from settings and are never printed or logged.
"""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import websockets

from ai_trader.ai.bedrock import BedrockAIProvider
from ai_trader.ai.jev import JevUnavailable, TypeSafeJevCritic
from ai_trader.ai.providers import build_ai_provider
from ai_trader.ai.remote_model import OpenAICompatibleAIProvider, RemoteAIUnavailable
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import PortfolioDecision
from ai_trader.domain.market import MarketState
from ai_trader.integrations.upstox.auth import MissingUpstoxCredential, UpstoxAuth
from ai_trader.integrations.upstox.instruments import fetch_instruments, resolve_instrument
from ai_trader.integrations.upstox.market_data import UpstoxMarketDataService
from ai_trader.integrations.upstox.protobuf.decoder import FeedType, V3DecodeError

CONNECTED_AND_DECODED_SNAPSHOT = "CONNECTED_AND_DECODED_SNAPSHOT"
CONNECTED_BUT_NO_LIVE_UPDATE = "CONNECTED_BUT_NO_LIVE_UPDATE"
PROBE_FAILED = "PROBE_FAILED"


@dataclass
class AIProbeResult:
    ok: bool
    lines: list[str] = field(default_factory=list)


@dataclass
class MarketProbeResult:
    ok: bool
    outcome: str
    state: MarketState | None
    lines: list[str] = field(default_factory=list)


CONNECTIVITY_PROMPT = (
    "You are the portfolio manager for a paper-trading system. Return only the requested "
    "structured JSON. Be conservative and explain decisions with thesis and evidence."
)

CONNECTIVITY_CONTEXT = (
    "Connectivity check. Instrument: INFY (NSE EQ). Last observed price: 1500.25 INR. "
    "Paper portfolio: cash 100000.00 INR, no open positions, no pending orders. "
    "No news context is supplied in this probe. "
    "Decide the appropriate portfolio action and return the full structured decision."
)


async def probe_ai(settings: Settings) -> AIProbeResult:
    lines: list[str] = []
    try:
        provider = build_ai_provider(settings)
    except ValueError as exc:
        return AIProbeResult(False, [f"AI CONFIG: {exc}"])

    lines.append(f"provider: {settings.ai_provider}")
    if isinstance(provider, OpenAICompatibleAIProvider):
        lines.append(f"server: {provider.server_identifier}")
        lines.append(f"model: {settings.ai_model}")
    elif isinstance(provider, BedrockAIProvider):
        lines.append(f"model: {provider.model_id}")
        lines.append(f"region: {provider.region or 'from AWS credential chain'}")
    else:  # pragma: no cover - factory only returns known implementations
        await provider.aclose()
        return AIProbeResult(False, [*lines, "error: unsupported provider implementation"])

    try:
        if isinstance(provider, OpenAICompatibleAIProvider):
            models = await provider.available_models()
        else:
            models = None
    except RemoteAIUnavailable as exc:
        await provider.aclose()
        return AIProbeResult(False, [*lines, "reachable: no", f"error: {exc}"])

    if models is None:
        lines.append("model_discovery: not_exposed_by_server")
    else:
        lines.append("model_discovery: ok")
        if settings.ai_model not in models:
            await provider.aclose()
            return AIProbeResult(
                False,
                [*lines, "reachable: yes", f"error: configured model not in {', '.join(models)}"],
            )

    try:
        decision, metadata = await provider.structured_completion(
            CONNECTIVITY_PROMPT,
            CONNECTIVITY_CONTEXT,
            PortfolioDecision,
            prompt_name="connectivity_probe",
            prompt_version="v1",
        )
    except RemoteAIUnavailable as exc:
        await provider.aclose()
        return AIProbeResult(
            False,
            [*lines, "reachable: yes", "structured_schema_valid: no", f"error: {exc}"],
        )

    lines.append("reachable: yes")
    lines.append(f"request_id: {metadata.request_id or 'not_returned'}")
    lines.append(f"latency_ms: {metadata.latency_ms}")
    lines.append(
        f"input_tokens: {metadata.input_tokens if metadata.input_tokens is not None else 'not_returned'}"
    )
    lines.append(
        f"output_tokens: {metadata.output_tokens if metadata.output_tokens is not None else 'not_returned'}"
    )
    estimated_cost = metadata.provider_metadata.get("estimated_total_usd")
    if isinstance(estimated_cost, (int, float)):
        lines.append(f"estimated_cost_usd: {estimated_cost:.6f}")
    lines.append("structured_schema_valid: yes")
    lines.append(f"action: {_primary_action(decision)}")
    lines.append(f"thesis: {decision.decision_summary[:200]}")
    await provider.aclose()
    return AIProbeResult(True, lines)


def _primary_action(decision: PortfolioDecision) -> str:
    if decision.proposals:
        return f"{decision.proposals[0].action.value}"
    return f"NO_PROPOSAL (cash_percent={decision.cash_percent})"


async def probe_jev(settings: Settings) -> AIProbeResult:
    if not settings.jev_api_key:
        return AIProbeResult(False, ["JEV CONFIG: JEV_API_KEY is not configured"])
    lines = [f"server: {settings.jev_base_url}", f"model: {settings.jev_model}"]
    critic = TypeSafeJevCritic(settings.jev_base_url, settings.jev_api_key, settings.jev_model)
    try:
        models = await critic.available_models()
    except Exception as exc:  # noqa: BLE001 - probe must report, not crash
        return AIProbeResult(
            False, [*lines, "models_endpoint: failed", f"error: {type(exc).__name__}"]
        )

    if settings.jev_model not in models:
        return AIProbeResult(
            False,
            [*lines, "models_endpoint: ok", f"error: configured model not in {', '.join(models)}"],
        )
    lines.append("models_endpoint: ok")

    proposal = PortfolioDecision(
        cash_percent=100,
        allocations=[],
        proposals=[],
        market_summary="connectivity probe",
        decision_summary="no trade",
    )
    try:
        assessment, metadata = await critic.assess(proposal)
    except JevUnavailable as exc:
        return AIProbeResult(False, [*lines, "systemone: failed", f"error: {exc}"])

    lines.append("systemone: ok")
    lines.append("typed_result: yes")
    lines.append(f"disposition: {assessment.bounded_decisions}")
    lines.append(f"evidence_strength: {assessment.score}")
    lines.append(f"latency_ms: {metadata.latency_ms}")
    return AIProbeResult(True, lines)


async def probe_market(
    settings: Settings, symbol: str | None = None, timeout_seconds: int = 45
) -> MarketProbeResult:
    target_symbol = symbol or settings.shadow_symbol
    lines = ["provider: upstox", f"symbol: {target_symbol}"]
    try:
        auth = UpstoxAuth(settings.upstox_api_base_url, settings.upstox_access_token or "")
    except MissingUpstoxCredential as exc:
        return MarketProbeResult(False, PROBE_FAILED, None, [*lines, f"credential: {exc}"])

    try:
        rows = await fetch_instruments()
        instrument = resolve_instrument(rows, target_symbol, settings.shadow_exchange)
    except Exception as exc:  # noqa: BLE001
        return MarketProbeResult(
            False,
            PROBE_FAILED,
            None,
            [*lines, f"instrument_resolution: failed ({type(exc).__name__})"],
        )

    lines.append(f"instrument_key: {instrument.instrument_key}")
    service = UpstoxMarketDataService(instrument, SystemClock())
    connection_id = str(uuid4())

    try:
        url = await auth.authorized_websocket_url()
    except Exception as exc:  # noqa: BLE001
        return MarketProbeResult(
            False, PROBE_FAILED, None, [*lines, f"connection: failed ({type(exc).__name__})"]
        )

    lines.append("connection: successful")
    state: MarketState | None = None
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as socket:
            await socket.send(
                '{"guid":"connectivity-probe","method":"sub","data":{"mode":"full",'
                f'"instrumentKeys":["{instrument.instrument_key}"]}}}}'
            )
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline and state is None:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    frame = await asyncio.wait_for(socket.recv(), timeout=remaining)
                except TimeoutError:
                    break
                if not isinstance(frame, bytes):
                    continue
                decoded_feed_type = None
                try:
                    from ai_trader.integrations.upstox.protobuf.decoder import decode_v3_frame

                    decoded_feed_type = decode_v3_frame(frame).feed_type
                except V3DecodeError:
                    continue
                if decoded_feed_type is FeedType.MARKET_INFO:
                    service.decoded_count += 1
                    continue
                try:
                    state = service.ingest(connection_id, frame)
                except ValueError:
                    continue
    except Exception as exc:  # noqa: BLE001
        return MarketProbeResult(
            False, PROBE_FAILED, None, [*lines, f"websocket: failed ({type(exc).__name__})"]
        )

    if state is None:
        lines.append("protobuf_decode: no instrument payload received within timeout")
        lines.append(f"outcome: {CONNECTED_BUT_NO_LIVE_UPDATE}")
        return MarketProbeResult(False, CONNECTED_BUT_NO_LIVE_UPDATE, None, lines)

    lines.append("protobuf_decode: successful")
    lines.append(
        f"message_type: {service.last_decoded.feed_type.value if service.last_decoded else 'unknown'}"
    )
    lines.append(f"last_price: {state.last_price}")
    lines.append(
        f"previous_close: {state.previous_close if state.previous_close is not None else 'n/a'}"
    )
    lines.append(f"exchange_timestamp: {state.recent_ticks[0].exchange_timestamp.isoformat()}")
    lines.append(f"outcome: {CONNECTED_AND_DECODED_SNAPSHOT}")
    return MarketProbeResult(True, CONNECTED_AND_DECODED_SNAPSHOT, state, lines)


def format_money(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
