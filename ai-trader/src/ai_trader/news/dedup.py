"""Deduplication primitives.

Dedup uses, in order: source-native id, canonical URL, content hash, and
normalized headline. All hashing is deterministic so replay reproduces clusters.
"""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlparse

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
# Boilerplate that appears across syndicated copies of one story.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "that",
        "this",
        "after",
        "over",
        "into",
        "amid",
        "says",
        "said",
    }
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash_for(text: str) -> str:
    return sha256_text(_WS_RE.sub(" ", text).strip().lower())


def headline_hash_for(headline: str) -> str:
    return sha256_text(normalize_headline(headline))


def normalize_headline(headline: str) -> str:
    """Casefold, strip punctuation, drop stopwords, collapse whitespace.

    Publishers vary only in case, punctuation and filler words for the same story.
    """
    text = unicodedata.normalize("NFKD", headline)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _PUNCT_RE.sub(" ", text.lower())
    tokens = [token for token in _WS_RE.split(text) if token and token not in _STOPWORDS]
    return " ".join(tokens)


def headline_similarity_key(headline: str) -> str:
    """Key for grouping near-duplicate headlines across sources."""
    return normalize_headline(headline) or " ".join(headline.lower().split()[:6])


def canonical_url_key(url: str) -> str:
    """Host + path identity, ignoring scheme, query and fragment."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    host = (parsed.netloc or "").lower().removeprefix("www.")
    return f"{host}{path}"


def dedup_keys(
    source_id: str, native_id: str | None, canonical_url: str, content_hash: str, headline_hash: str
) -> dict[str, str]:
    """Stable identity keys used to look up an existing item."""
    keys = {
        "url": sha256_text(f"url|{canonical_url_key(canonical_url)}"),
        "content": sha256_text(f"content|{content_hash}"),
        "headline": sha256_text(f"headline|{headline_hash}"),
    }
    if native_id:
        keys["native"] = sha256_text(f"native|{source_id}|{native_id}")
    return keys


def utc_now() -> datetime:
    return datetime.now(UTC)
