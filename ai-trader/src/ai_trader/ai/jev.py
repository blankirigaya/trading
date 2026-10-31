import asyncio
import logging
from abc import ABC, abstractmethod
from time import perf_counter

import httpx

from ai_trader.config import Settings
from ai_trader.domain.decisions import CriticAssessment, PortfolioDecision, ProviderMetadata
from ai_trader.news.review_context import NewsReviewBundle

logger = logging.getLogger(__name__)


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


class DisabledJevCritic(JevCritic):
    """Stand-in used when Jev is disabled by configuration.

    Agrees with everything and records the stage as SKIPPED, so the journal
    shows the critique was deliberately not performed rather than faked.
    """

    async def assess(
        self, proposal: PortfolioDecision, news: NewsReviewBundle | None = None
    ) -> tuple[CriticAssessment, ProviderMetadata]:
        return (
            CriticAssessment(
                score=0.0,
                agrees=True,
                disagreement_signals=[],
                critique="Jev disabled by configuration; no critique performed.",
                bounded_decisions=["SKIPPED"],
            ),
            ProviderMetadata(
                provider="jev-disabled",
                model="none",
                prompt_name="critic",
                prompt_version="v2",
                latency_ms=0,
            ),
        )


def build_critic(settings: Settings) -> JevCritic:
    """Select the Jev critic from settings.

    Disabled means skipped and journalled as SKIPPED. Enabled without a key
    stays fail-closed: the live critic raises JevUnavailable instead of
    approving anything.
    """
    if not settings.jev_enabled:
        return DisabledJevCritic()
    return TypeSafeJevCritic(
        settings.jev_base_url, settings.jev_api_key or "", settings.jev_model
    )


class TypeSafeJevCritic(JevCritic):
    """Jev critic backed by the TypeSafe System One API.

    The proposal (plus optional news context) is submitted as ``state`` with three
    named questions: ``sound`` (yes/no), ``evidence`` (0-4 rubric score), and
    ``disposition`` (APPROVE / CHALLENGE / REQUEST_RECONSIDERATION). Answers are
    mapped onto :class:`CriticAssessment`. Anything unexpected fails closed.
    """

    _APPROVE = "APPROVE"
    _CHALLENGE = "CHALLENGE"
    _RECONSIDER = "REQUEST_RECONSIDERATION"
    # An APPROVE verdict is only trusted when the model itself assigns at least
    # this much soundness; below it the approval is incoherent and fails closed.
    _APPROVE_SOUND_FLOOR = 0.15
    # Transient transport failures (timeouts, refused connections, 429/5xx) are
    # retried once with a short backoff: a momentary blip must not kill a review.
    # Anything else (4xx, malformed payloads) fails closed immediately.
    _MAX_ATTEMPTS = 2
    _RETRY_DELAY_SECONDS = 2.0
    _RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
    _EVIDENCE_LEVELS = [
        "No supporting evidence",
        "Weak evidence",
        "Moderate evidence",
        "Strong evidence",
        "Very strong evidence",
    ]

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def available_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            response.raise_for_status()
        body = response.json()
        items = body.get("data", body.get("models", []))
        return [
            str(item.get("id", item.get("name")))
            for item in items
            if isinstance(item, dict) and (item.get("id") or item.get("name"))
        ]

    async def assess(
        self, proposal: PortfolioDecision, news: NewsReviewBundle | None = None
    ) -> tuple[CriticAssessment, ProviderMetadata]:
        start = perf_counter()
        state: dict[str, object] = {"proposal": proposal.model_dump(mode="json")}
        sound_instructions = (
            "Is this trade proposal sound and justified by its market summary and thesis?"
        )
        if news is not None and not news.is_empty:
            state["news_context"] = news.as_prompt()
            # The critic must be able to see whether the proposal actually reflects the
            # news, and must not mistake analyst confidence for trading confidence.
            sound_instructions += (
                " The proposal must be consistent with the ORIGINAL SOURCE INFORMATION"
                " in the news context, and must not treat an AI NEWS INTERPRETATION's"
                " confidence as trading conviction. Challenge a proposal that appears to"
                " follow a headline keyword rather than the underlying facts."
            )
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                "sound": {"type": "noul", "instructions": sound_instructions},
                "evidence": {
                    "type": "score",
                    "instructions": "Rate the strength of the evidence supporting this trade proposal.",
                    "criteria": list(self._EVIDENCE_LEVELS),
                },
                    "disposition": {
                        "type": "choice",
                        "instructions": (
                            "Choose how to handle this trade proposal. "
                            "Choose APPROVE when the proposal is reasonable as stated, "
                            "even if the opportunity is modest; do not demand perfection. "
                            "Choose CHALLENGE only for a specific, statable flaw, "
                            "and REQUEST_RECONSIDERATION only when the trade should "
                            "not proceed as proposed. Your choice must be consistent "
                            "with your soundness judgment: do not challenge a proposal "
                            "you judged sound, and do not approve one you judged unsound."
                        ),
                        "criteria": {
                            self._APPROVE: "The proposal is reasonable; proceed.",
                            self._CHALLENGE: "The proposal has a specific flaw; send it back with criticism.",
                            self._RECONSIDER: "The trade should not proceed as proposed; a full rethink is required.",
                        },
                    },
            },
        }
        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(1, self._MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/v1/systemone",
                        headers=self._headers(),
                        json=payload,
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError as exc:
                    if attempt >= self._MAX_ATTEMPTS or not self._transport_retryable(exc):
                        raise JevUnavailable("Jev critique request failed") from exc
                    logger.warning(
                        "Jev critique request failed transiently; retrying",
                        extra={
                            "attempt": attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    await asyncio.sleep(self._RETRY_DELAY_SECONDS)
        if response is None:  # pragma: no cover - loop always assigns or raises
            raise JevUnavailable("Jev critique request failed")
        body = response.json()
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise JevUnavailable("Jev returned a malformed critique")
        try:
            sound = self._noul_answer(answers.get("sound"))
            evidence_score, evidence_confidence = self._score_answer(answers.get("evidence"))
            disposition, disposition_confidence, runner_up = self._choice_answer(
                answers.get("disposition")
            )
        except (TypeError, ValueError) as exc:
            raise JevUnavailable("Jev returned a malformed critique") from exc

        # The discrete disposition is the verdict; the soundness score is supporting
        # information. Scalar soundness judgments cluster low and overlap between
        # weak and solid proposals, so a fixed cutoff cannot separate them, while
        # the explicit choice does. Incoherent approvals still fail closed below.
        agrees = disposition == self._APPROVE and sound >= self._APPROVE_SOUND_FLOOR
        score = min(1.0, max(0.0, evidence_score / (len(self._EVIDENCE_LEVELS) - 1)))
        signals: list[str] = []
        if disposition != self._APPROVE:
            signals.append(f"disposition={disposition}")
        if runner_up is not None:
            signals.append(f"alternative={runner_up}")
        if not agrees:
            signals.append("proposal_not_sound")
        evidence_label = self._EVIDENCE_LEVELS[min(len(self._EVIDENCE_LEVELS) - 1, round(score * (len(self._EVIDENCE_LEVELS) - 1)))]
        critique = (
            f"Jev disposition: {disposition} (confidence {disposition_confidence:.0%}). "
            f"Proposal soundness: {sound:.0%}. Evidence: {evidence_label} "
            f"(score {evidence_score:.1f}/{len(self._EVIDENCE_LEVELS) - 1}, "
            f"confidence {evidence_confidence:.0%})."
        )
        assessment = CriticAssessment(
            score=score,
            agrees=agrees,
            disagreement_signals=signals,
            critique=critique,
            bounded_decisions=[disposition],
        )
        return assessment, ProviderMetadata(
            provider="typesafe-jev",
            model=str(body.get("model", self.model)),
            request_id=response.headers.get("x-request-id"),
            prompt_name="critic",
            prompt_version="v2",
            latency_ms=round((perf_counter() - start) * 1000),
        )

    @staticmethod
    def _transport_retryable(exc: httpx.HTTPError) -> bool:
        """Whether a failed Jev call is worth one retry.

        Timeouts, refused connections, and truncated responses are transient by
        nature; 429/5xx ask for patience explicitly. Anything else (4xx, TLS,
        malformed payloads) will fail the same way again, so fail closed
        immediately instead.
        """
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            return exc.response.status_code in TypeSafeJevCritic._RETRYABLE_STATUS_CODES
        return False

    @staticmethod
    def _noul_answer(answer: object) -> float:
        """Extract the yes-probability from a ``noul`` answer."""
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise ValueError("missing noul answer")
        value = answer.get("noul")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("malformed noul answer")
        return min(1.0, max(0.0, float(value)))

    @staticmethod
    def _score_answer(answer: object) -> tuple[float, float]:
        """Extract ``(score, confidence)`` from a ``score`` answer."""
        if not isinstance(answer, dict) or answer.get("type") != "score":
            raise ValueError("missing score answer")
        value = answer.get("score")
        confidence = answer.get("confidence", 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("malformed score answer")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("malformed score confidence")
        return float(value), min(1.0, max(0.0, float(confidence)))

    @classmethod
    def _choice_answer(cls, answer: object) -> tuple[str, float, str | None]:
        """Extract ``(choice, confidence, runner_up)`` from a ``choice`` answer."""
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ValueError("missing choice answer")
        choice = answer.get("choice")
        confidence = answer.get("confidence", 0.0)
        if not isinstance(choice, str) or not choice:
            raise ValueError("malformed choice answer")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("malformed choice confidence")
        runner_up: str | None = None
        probabilities = answer.get("probabilities")
        if isinstance(probabilities, dict):
            contenders = sorted(
                (
                    (str(name), float(prob))
                    for name, prob in probabilities.items()
                    if name != choice and isinstance(prob, (int, float)) and not isinstance(prob, bool)
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            if contenders and contenders[0][1] >= 0.25:
                runner_up = f"{contenders[0][0]} {contenders[0][1]:.0%}"
        if choice not in (cls._APPROVE, cls._CHALLENGE, cls._RECONSIDER):
            raise ValueError(f"unknown disposition: {choice}")
        return choice, min(1.0, max(0.0, float(confidence))), runner_up

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
