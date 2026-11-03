"""Typed AI failures that carry enough detail to act on.

A bare "schema validation failed" is undiagnosable: the operator cannot tell an
out-of-date prompt from a model that omitted one optional field, and the retry
wrapper cannot tell the model what to fix. These helpers keep the field-level
reason so it reaches the log, the retry prompt, and the decision record.
"""

from __future__ import annotations

MAX_SCHEMA_DETAIL_CHARS = 400


class SchemaValidationError(ValueError):
    """Model output did not satisfy the requested response schema.

    Subclasses ``ValueError`` on purpose: :class:`RetryingAIProvider` retries
    schema failures and must not start retrying transport faults.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = (detail or "").strip()[:MAX_SCHEMA_DETAIL_CHARS]
        base = "AI response failed schema validation"
        super().__init__(f"{base}: {self.detail}" if self.detail else base)


def summarize_validation_errors(exc: BaseException) -> str:
    """Return a compact ``field: reason`` summary for a Pydantic ValidationError.

    Falls back to the exception's own text when it is not a Pydantic error, so a
    JSON decode fault still produces something an operator can read.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc).strip()[:MAX_SCHEMA_DETAIL_CHARS]
    try:
        parts = []
        for item in errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "__root__")
            parts.append(f"{location or 'response'}: {item.get('msg', 'invalid')}")
    except Exception:  # noqa: BLE001 - a summary must never mask the real failure
        return str(exc).strip()[:MAX_SCHEMA_DETAIL_CHARS]
    return "; ".join(parts)[:MAX_SCHEMA_DETAIL_CHARS]
