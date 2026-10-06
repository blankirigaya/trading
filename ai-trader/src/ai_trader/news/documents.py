"""Linked filing/release documents for trusted official sources.

Downloads are domain-allowlisted and content-addressed. Arbitrary URLs proposed by
an AI are never fetched: `discover_document_urls` is only ever called with items
whose source is configured as trusted.
"""

import asyncio
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ai_trader.domain.news import (
    NewsDocument,
    NewsItem,
    SourceType,
    TrustLevel,
)
from ai_trader.news.provider import USER_AGENT, clean_text

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
EXTRACTED_TEXT_LIMIT = 20_000
DATA_RAW = Path("data/raw/news_documents")

_LINK_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_PDF_HINT = re.compile(r"\.pdf(?:$|\?)", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_RE = re.compile(r"</(p|div|li|tr|h[1-6])>", re.IGNORECASE)

TRUSTED_SOURCE_TYPES = {SourceType.OFFICIAL, SourceType.COMPANY}
TRUSTED_TRUST_LEVELS = {TrustLevel.PRIMARY, TrustLevel.HIGH}


class DocumentPolicyError(RuntimeError):
    """A document download was refused by policy."""


def is_trusted_source(item: NewsItem) -> bool:
    """Documents are only fetched for official and company sources."""
    return item.source_type in TRUSTED_SOURCE_TYPES and item.trust_level in (TRUSTED_TRUST_LEVELS)


def host_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    if not allowed_hosts:
        return False
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    return any(host == candidate.lower().removeprefix("www.") for candidate in allowed_hosts)


def discover_document_urls(
    item: NewsItem, body_html: str, allowed_hosts: tuple[str, ...]
) -> list[str]:
    """Extract document links from a trusted item's own page.

    Only hosts on the source's allowlist are returned, and only from that page.
    """
    if not is_trusted_source(item):
        return []
    from urllib.parse import urljoin

    found: list[str] = []
    for href in _LINK_RE.findall(body_html):
        absolute = urljoin(item.canonical_url, href)
        if host_allowed(absolute, allowed_hosts) and _PDF_HINT.search(absolute):
            if absolute not in found:
                found.append(absolute)
    return found


def extract_text(body: bytes, content_type: str) -> tuple[str, bool]:
    """Best-effort searchable text. PDF text extraction is not attempted here."""
    if "pdf" in content_type.lower():
        return "", True
    text = body.decode("utf-8", errors="ignore")
    title = _TITLE_RE.search(text)
    stripped = _TAG_RE.sub(" ", text)
    stripped = _BLOCK_RE.sub("\n", stripped)
    extracted = clean_text(stripped)
    if title:
        heading = clean_text(title.group(1))
        if heading:
            extracted = f"{heading}\n\n{extracted}"
    truncated = len(extracted) > EXTRACTED_TEXT_LIMIT
    return extracted[:EXTRACTED_TEXT_LIMIT], truncated


async def download_document(
    client: httpx.AsyncClient,
    url: str,
    allowed_hosts: tuple[str, ...],
    storage_dir: Path | None = None,
) -> NewsDocument:
    """Download one allowlisted document and record its SHA-256."""
    if not host_allowed(url, allowed_hosts):
        raise DocumentPolicyError(f"host for {url} is not on the allowlist")
    if not url.startswith(("http://", "https://")):
        raise DocumentPolicyError("only http and https documents are permitted")

    response = await client.get(url, headers={"User-Agent": USER_AGENT})
    if response.status_code >= 400:
        raise DocumentPolicyError(f"document returned HTTP {response.status_code}")
    payload = response.content
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise DocumentPolicyError("document exceeds the maximum permitted size")

    digest = sha256(payload).hexdigest()
    content_type = response.headers.get("content-type", "application/octet-stream")
    downloaded_at = datetime.now(UTC)

    local_path: str | None = None
    extracted: str | None = None
    truncated = False
    if storage_dir is not None:
        # Filesystem writes are blocking; keep them off the event loop.
        directory = Path(storage_dir)
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        suffix = ".pdf" if "pdf" in content_type.lower() else ".html"
        target = directory / f"{digest[:32]}{suffix}"
        await asyncio.to_thread(target.write_bytes, payload)
        local_path = str(target)

    if "pdf" not in content_type.lower():
        extracted, truncated = extract_text(payload, content_type)
    else:
        truncated = True

    return NewsDocument(
        document_id=digest[:32],
        url=url,
        sha256=digest,
        content_type=content_type,
        byte_size=len(payload),
        downloaded_at=downloaded_at,
        local_path=local_path,
        extracted_text=extracted,
        text_truncated=truncated,
    )


def default_storage_dir() -> Path:
    return DATA_RAW
