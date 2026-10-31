"""Aggregate Bedrock inference usage (tokens and estimated spend) for the dashboard.

Counts every journalled AI call: initial portfolio reviews, Jev-triggered
reconsiderations, and news analyses. Jev's own System One usage is TypeSafe
billing, not Bedrock, so it is reported separately and never mixed into cost.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ai_trader.domain.decisions import ProviderMetadata
from ai_trader.domain.news import NewsAnalysis
from ai_trader.journal.service import DecisionRecord


def _tokens(meta: ProviderMetadata | dict[str, Any] | None) -> tuple[int, int, float | None]:
    """Return ``(input_tokens, output_tokens, estimated_total_usd)``."""
    if meta is None:
        return 0, 0, None
    if isinstance(meta, ProviderMetadata):
        values: dict[str, Any] = {
            "input_tokens": meta.input_tokens,
            "output_tokens": meta.output_tokens,
            "estimated_total_usd": meta.provider_metadata.get("estimated_total_usd"),
        }
    else:
        values = meta
    return (
        _as_int(values.get("input_tokens")),
        _as_int(values.get("output_tokens")),
        _as_float(values.get("estimated_total_usd")),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def summarize_usage(
    records: list[DecisionRecord],
    analyses: list[NewsAnalysis],
    *,
    now: datetime | None = None,
    cost_configured: bool = False,
) -> dict[str, object]:
    """Summarize AI usage across reviews, reconsiderations, and news analyses."""
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(hours=24)

    def bucket() -> dict[str, Any]:
        return {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": None,
        }

    def add(target: dict[str, Any], meta: ProviderMetadata | dict[str, Any] | None) -> None:
        inbound, outbound, cost = _tokens(meta)
        target["calls"] = int(target["calls"]) + 1
        target["input_tokens"] = int(target["input_tokens"]) + inbound
        target["output_tokens"] = int(target["output_tokens"]) + outbound
        if cost is not None:
            known = target["estimated_cost_usd"]
            base = float(known) if isinstance(known, (int, float)) else 0.0
            target["estimated_cost_usd"] = round(base + cost, 6)

    total = bucket()
    by_model: dict[str, dict[str, Any]] = {}
    last_24h = bucket()
    sources = {"reviews": bucket(), "reconsiderations": bucket(), "news": bucket()}

    def add_call(
        meta: ProviderMetadata | dict[str, Any] | None,
        *,
        source: str,
        model: str,
        at: datetime | None,
    ) -> None:
        name = model or "unknown"
        entry = by_model.setdefault(name, bucket())
        for target in (total, entry, sources[source]):
            add(target, meta)
        moment_at = _as_aware(at)
        if moment_at is not None and moment_at >= cutoff:
            add(last_24h, meta)

    for record in records:
        when = _as_aware(record.timestamp)
        add_call(record.initial_provider_metadata, source="reviews",
                 model=record.initial_provider_metadata.model, at=when)
        if record.reconsideration_performed and record.reconsideration_provider_metadata is not None:
            add_call(record.reconsideration_provider_metadata, source="reconsiderations",
                     model=record.reconsideration_provider_metadata.model, at=when)

    for analysis in analyses:
        meta = analysis.provider_metadata
        model = meta.get("model") if isinstance(meta, dict) else None
        add_call(meta if isinstance(meta, dict) else None, source="news",
                 model=str(model) if model else "unknown",
                 at=analysis.analysis_completed_at or analysis.analysis_started_at)

    return {
        "calls": total["calls"],
        "input_tokens": total["input_tokens"],
        "output_tokens": total["output_tokens"],
        "estimated_cost_usd": total["estimated_cost_usd"],
        "cost_configured": cost_configured,
        "by_model": by_model,
        "last_24h": last_24h,
        "sources": sources,
    }


def ist_day_start(now: datetime) -> datetime:
    """Midnight IST at the start of ``now``'s trading day, as an aware datetime."""
    day = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo("Asia/Kolkata"))


def spend_since(
    records: list[DecisionRecord],
    analyses: list[NewsAnalysis],
    *,
    since: datetime,
) -> float:
    """Estimated Bedrock spend since ``since``.

    Sums estimated per-call costs from journalled metadata. Calls without a
    cost estimate (recorded before rates were configured) contribute 0.0, so
    treat the total as a lower bound, never an exact bill.
    """
    total = 0.0
    for record in records:
        when = _as_aware(record.timestamp)
        if when is None or when < since:
            continue
        for meta in (
            record.initial_provider_metadata,
            record.reconsideration_provider_metadata
            if record.reconsideration_performed
            else None,
        ):
            _, _, cost = _tokens(meta)
            if cost is not None:
                total += cost
    for analysis in analyses:
        when = _as_aware(analysis.analysis_completed_at or analysis.analysis_started_at)
        if when is None or when < since:
            continue
        analysis_meta = analysis.provider_metadata
        _, _, cost = _tokens(analysis_meta if isinstance(analysis_meta, dict) else None)
        if cost is not None:
            total += cost
    return round(total, 6)
