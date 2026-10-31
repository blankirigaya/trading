"""Jev critic selection: live, disabled, and fail-closed paths."""

from ai_trader.ai.jev import DisabledJevCritic, TypeSafeJevCritic, build_critic
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioAction, PortfolioDecision, TradeProposal
from ai_trader.domain.orders import OrderSide


def _decision() -> PortfolioDecision:
    from decimal import Decimal

    return PortfolioDecision(
        cash_percent=90.0,
        allocations=[],
        proposals=[
            TradeProposal(
                proposal_id="p-1",
                symbol="INFY",
                action=PortfolioAction.OPEN_POSITION,
                side=OrderSide.BUY,
                capital_allocation_inr=Decimal("15000"),
                quantity_shares=10,
                confidence=0.6,
                thesis="test",
                evidence=[],
                conditions_that_change_thesis=[],
            )
        ],
        market_summary="m",
        decision_summary="d",
    )


async def test_disabled_critic_skips_without_faking_approval() -> None:
    assessment, metadata = await DisabledJevCritic().assess(_decision())
    assert assessment.agrees is True
    assert assessment.bounded_decisions == ["SKIPPED"]
    assert "disabled" in assessment.critique.lower()
    assert metadata.provider == "jev-disabled"


def test_build_critic_selects_live_only_when_enabled_with_key() -> None:
    live = build_critic(
        Settings(jev_enabled=True, jev_api_key="k", jev_model="jev-latest")
    )
    assert isinstance(live, TypeSafeJevCritic)
    assert isinstance(build_critic(Settings(jev_enabled=False, jev_api_key="k")), DisabledJevCritic)
    # Enabled without a key stays fail-closed: the live critic raises on use.
    keyless = build_critic(Settings(jev_enabled=True, jev_api_key=""))
    assert isinstance(keyless, TypeSafeJevCritic)


async def test_enabled_without_key_raises_instead_of_approving(monkeypatch) -> None:
    import httpx

    from ai_trader.ai.jev import JevUnavailable

    class _FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FailingClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object) -> object:
            raise httpx.ConnectError("no network in unit tests")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)  # type: ignore[arg-type]
    critic = build_critic(Settings(jev_enabled=True, jev_api_key=""))
    assert isinstance(critic, TypeSafeJevCritic)
    try:
        await critic.assess(_decision())
    except JevUnavailable:
        return
    raise AssertionError("expected JevUnavailable")


async def test_disposition_is_the_verdict_not_the_scalar(monkeypatch) -> None:
    import httpx

    async def assess_with(sound: float, disposition: str):
        from ai_trader.ai.jev import TypeSafeJevCritic

        answers = {
            "answers": {
                "sound": {"type": "noul", "noul": sound},
                "evidence": {"type": "score", "score": 2.0, "confidence": 0.8},
                "disposition": {"type": "choice", "choice": disposition, "confidence": 0.8},
            },
            "model": "jev-latest",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        class _CannedClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_CannedClient":
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def post(self, *args: object, **kwargs: object):
                request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")
                return httpx.Response(200, json=answers, request=request)

        monkeypatch.setattr(httpx, "AsyncClient", _CannedClient)  # type: ignore[arg-type]
        critic = build_critic(Settings(jev_enabled=True, jev_api_key="k"))
        assert isinstance(critic, TypeSafeJevCritic)
        return await critic.assess(_decision())

    # APPROVE is trusted even when the scalar is middling...
    assessment, _ = await assess_with(0.4, "APPROVE")
    assert assessment.agrees is True
    assert assessment.bounded_decisions == ["APPROVE"]
    assert assessment.score == 0.5
    # ...CHALLENGE forces a rethink even with a decent scalar...
    assessment, _ = await assess_with(0.7, "CHALLENGE")
    assert assessment.agrees is False
    assert assessment.bounded_decisions == ["CHALLENGE"]
    # ...and an incoherent APPROVE (near-zero soundness) still fails closed.
    assessment, _ = await assess_with(0.05, "APPROVE")
    assert assessment.agrees is False


def _approve_answers() -> dict:
    return {
        "answers": {
            "sound": {"type": "noul", "noul": 0.8},
            "evidence": {"type": "score", "score": 3.0, "confidence": 0.9},
            "disposition": {"type": "choice", "choice": "APPROVE", "confidence": 0.9},
        },
        "model": "jev-latest",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _ok_response(answers: dict):
    import httpx

    request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")
    return httpx.Response(200, json=answers, request=request)


async def test_transient_transport_error_is_retried_once_then_succeeds(monkeypatch) -> None:
    import httpx

    from ai_trader.ai import jev as jev_module
    from ai_trader.ai.jev import TypeSafeJevCritic

    calls = {"posts": 0}

    class _FlakyClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FlakyClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object):
            calls["posts"] += 1
            if calls["posts"] == 1:
                raise httpx.ConnectError("simulated blip")
            return _ok_response(_approve_answers())

    monkeypatch.setattr(httpx, "AsyncClient", _FlakyClient)  # type: ignore[arg-type]
    monkeypatch.setattr(jev_module.TypeSafeJevCritic, "_RETRY_DELAY_SECONDS", 0)
    critic = build_critic(Settings(jev_enabled=True, jev_api_key="k"))
    assert isinstance(critic, TypeSafeJevCritic)
    assessment, _ = await critic.assess(_decision())
    assert assessment.agrees is True
    assert calls["posts"] == 2


async def test_non_retryable_transport_error_fails_fast_without_retry(monkeypatch) -> None:
    import httpx

    from ai_trader.ai.jev import JevUnavailable, TypeSafeJevCritic

    calls = {"posts": 0}

    class _RefusingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_RefusingClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object):
            calls["posts"] += 1
            request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")
            response = httpx.Response(400, json={"detail": "bad request"}, request=request)
            raise httpx.HTTPStatusError("client error", request=request, response=response)

    monkeypatch.setattr(httpx, "AsyncClient", _RefusingClient)  # type: ignore[arg-type]
    critic = build_critic(Settings(jev_enabled=True, jev_api_key="k"))
    assert isinstance(critic, TypeSafeJevCritic)
    try:
        await critic.assess(_decision())
    except JevUnavailable as exc:
        assert calls["posts"] == 1
        assert isinstance(exc.__cause__, httpx.HTTPStatusError)
        return
    raise AssertionError("expected JevUnavailable")


async def test_persistent_transient_failure_keeps_cause_chain(monkeypatch) -> None:
    import httpx

    from ai_trader.ai import jev as jev_module
    from ai_trader.ai.jev import JevUnavailable

    calls = {"posts": 0}

    class _DownClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_DownClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object):
            calls["posts"] += 1
            raise httpx.ReadTimeout("simulated timeout")

    monkeypatch.setattr(httpx, "AsyncClient", _DownClient)  # type: ignore[arg-type]
    monkeypatch.setattr(jev_module.TypeSafeJevCritic, "_RETRY_DELAY_SECONDS", 0)
    critic = build_critic(Settings(jev_enabled=True, jev_api_key="k"))
    try:
        await critic.assess(_decision())
    except JevUnavailable as exc:
        assert calls["posts"] == 2
        assert isinstance(exc.__cause__, httpx.ReadTimeout)
        return
    raise AssertionError("expected JevUnavailable")
