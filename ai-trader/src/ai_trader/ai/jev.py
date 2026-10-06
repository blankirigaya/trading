from abc import ABC, abstractmethod
from time import perf_counter

import httpx

from ai_trader.domain.decisions import CriticAssessment, PortfolioDecision, ProviderMetadata
from ai_trader.news.review_context import NewsReviewBundle


class JevCritic(ABC):
    @abstractmethod
    async def assess(
        self, proposal: PortfolioDecision, news: NewsReviewBundle | None = None
    ) -> tuple[CriticAssessment, ProviderMetadata]: ...


class MockJevCritic(JevCritic):
    async def assess(
        self, proposal: PortfolioDecision, news: NewsReviewBundle | None = None
    ) -> tuple[CriticAssessment, ProviderMetadata]:
        return (
            CriticAssessment(
                score=0.8,
                agrees=True,
                disagreement_signals=[],
                critique="Mock critic agrees; no live Jev integration is configured.",
                bounded_decisions=["No change requested."],
            ),
            ProviderMetadata(
                provider="mock-jev",
                model="mock-jev-v1",
                prompt_name="critic",
                prompt_version="v1",
            ),
        )


class JevUnavailable(RuntimeError):
    pass


class TypeSafeJevCritic(JevCritic):
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def available_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            response.raise_for_status()
        return [str(item["id"]) for item in response.json().get("data", [])]

    async def assess(
        self, proposal: PortfolioDecision, news: NewsReviewBundle | None = None
    ) -> tuple[CriticAssessment, ProviderMetadata]:
        start = perf_counter()
        instructions = "Return JSON: score 0..1, agrees boolean, disagreement_signals list, critique, bounded_decisions list. Recommend APPROVE, CHALLENGE, or REQUEST_RECONSIDERATION."
        if news is not None and not news.is_empty:
            # The critic must be able to see whether the proposal actually reflects the
            # news, and must not mistake analyst confidence for trading confidence.
            instructions += (
                " News context follows in two separated blocks: ORIGINAL SOURCE INFORMATION"
                " and AI NEWS INTERPRETATION. Check that the proposal is consistent with the"
                " source material and that it does not treat an interpretation's confidence as"
                " trading conviction. Challenge a proposal that appears to follow a headline"
                " keyword rather than the underlying facts."
            )
        payload = {
            "model": self.model,
            "input": proposal.model_dump(mode="json"),
            "instructions": instructions,
        }
        if news is not None and not news.is_empty:
            payload["news_context"] = news.as_prompt()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/v1/systemone", headers=self._headers(), json=payload
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JevUnavailable("Jev critique request failed") from exc
        body = response.json()
        content = body.get("output", body.get("response", body))
        try:
            assessment = CriticAssessment.model_validate(content)
        except (TypeError, ValueError) as exc:
            raise JevUnavailable("Jev returned malformed critique") from exc
        return assessment, ProviderMetadata(
            provider="typesafe-jev",
            model=self.model,
            request_id=response.headers.get("x-request-id"),
            prompt_name="critic",
            prompt_version="v1",
            latency_ms=round((perf_counter() - start) * 1000),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
