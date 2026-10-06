"""News provider interface and the generic RSS/Atom provider.

A provider's only job is to fetch permitted public material and return typed
`NewsItem`s. It performs no interpretation and never produces a trading action.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

from ai_trader.domain.news import NewsCategory, NewsItem, NewsSource
from ai_trader.news.dedup import content_hash_for, headline_hash_for

USER_AGENT = "ai-trader/0.2 (news intelligence; paper trading)"
DEFAULT_TIMEOUT = 25
MAX_ITEMS_PER_PROVIDER = 40

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Conservative identifier for a symbol mentioned in free text. Resolution is
# advisory only; the AI decides what a headline means.
_SYMBOL_RE = re.compile(r"\b([A-Z]{2,10})\b")


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of one poll. Never raises for an expected upstream failure."""

    source_id: str
    ok: bool
    items: tuple[NewsItem, ...] = ()
    error: str | None = None
    http_status: int | None = None

    @property
    def item_count(self) -> int:
        return len(self.items)


class NewsProvider(ABC):
    """Common contract for every news source."""

    def __init__(self, source: NewsSource, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.source = source
        self.timeout = timeout

    @abstractmethod
    async def poll(self, client: httpx.AsyncClient) -> ProviderResult: ...

    async def _fail(self, error: str, status: int | None = None) -> ProviderResult:
        return ProviderResult(self.source.source_id, False, error=error, http_status=status)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    without_tags = _TAG_RE.sub(" ", value)
    return _WS_RE.sub(" ", unescape(without_tags)).strip()


def extract_symbols(text: str, known: set[str]) -> tuple[str, ...]:
    """Return known trading symbols mentioned in text. Advisory, never a signal."""
    upper = text.upper()
    found = {symbol for symbol in known if re.search(rf"\b{re.escape(symbol)}\b", upper)}
    return tuple(sorted(found))


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(entry: ET.Element, names: tuple[str, ...]) -> str | None:
    for child in entry:
        if _localname(child.tag) not in names:
            continue
        raw = child.text
        if raw is None:
            continue
        stripped = raw.strip()
        if stripped:
            return stripped
    return None


def _first_link(entry: ET.Element) -> str | None:
    for child in entry:
        if _localname(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
            text = child.text
            if text is not None and text.strip():
                return text.strip()
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    # RFC 822 first (RSS), then ISO 8601 (Atom).
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def canonicalize_url(url: str, base: str | None = None) -> str:
    """Strip tracking parameters and fragments so duplicates collapse."""
    absolute = urljoin(base, url) if base else url
    parsed = urlparse(absolute)
    keep = [
        (key, value)
        for key, value in parse_qsl_safe(parsed.query)
        if not key.lower().startswith(("utm_", "fbclid", "gclid", "ref", "source"))
    ]
    query = "&".join(f"{key}={value}" for key, value in keep)
    return parsed._replace(query=query, fragment="").geturl()


def parse_qsl_safe(query: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl

    try:
        return parse_qsl(query)
    except ValueError:
        return []


def _feed_entries(root: ET.Element) -> list[ET.Element]:
    """Return feed entries from RSS 2.0 (``<rss><channel><item>``) and Atom (``<feed><entry>``)."""
    direct = [child for child in root if _localname(child.tag) in ("item", "entry")]
    if direct:
        return direct
    for child in root:
        if _localname(child.tag) == "channel":
            return [item for item in child if _localname(item.tag) == "item"]
    return []


class GenericRSSProvider(NewsProvider):
    """Parses any RSS 2.0 or Atom feed using only public endpoints."""

    async def poll(self, client: httpx.AsyncClient) -> ProviderResult:
        try:
            response = await client.get(self.source.homepage, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            return await self._fail(f"request failed: {type(exc).__name__}")
        if response.status_code >= 400:
            return await self._fail(
                f"upstream returned HTTP {response.status_code}", response.status_code
            )
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            return await self._fail(f"malformed feed XML: {exc}")

        received_at = datetime.now(UTC)
        items: list[NewsItem] = []
        for entry in _feed_entries(root)[:MAX_ITEMS_PER_PROVIDER]:
            item = self._to_item(entry, received_at)
            if item is not None:
                items.append(item)
        return ProviderResult(self.source.source_id, True, tuple(items), http_status=200)

    def _to_item(self, entry: ET.Element, received_at: datetime) -> NewsItem | None:
        title = clean_text(_first_text(entry, ("title",)))
        if not title:
            return None
        link = _first_link(entry) or _first_text(entry, ("guid", "id")) or ""
        canonical = canonicalize_url(link, self.source.homepage) if link else self.source.homepage
        published = (
            _parse_date(_first_text(entry, ("pubdate", "published", "updated", "date")))
            or received_at
        )
        description = clean_text(
            _first_text(entry, ("description", "summary", "content", "encoded"))
        )
        native_id = _first_text(entry, ("guid", "id"))
        symbols = (
            extract_symbols(f"{title} {description}", set(self.source.notes.split()))
            if (self.source.notes)
            else ()
        )

        return NewsItem(
            item_id=content_hash_for(f"{self.source.source_id}|{canonical}|{title}")[:32],
            source_id=self.source.source_id,
            source_name=self.source.name,
            source_type=self.source.source_type,
            trust_level=self.source.trust_level,
            source_native_id=native_id,
            headline=title,
            canonical_url=canonical,
            url=link or canonical,
            published_at=published,
            received_at=received_at,
            category=self.source.default_category,
            symbols=symbols,
            content_hash=content_hash_for(description or title),
            headline_hash=headline_hash_for(title),
            excerpt=description[:600] or None,
            content=description or None,
            raw_metadata={"feed": self.source.homepage},
        )


async def gather(provider: NewsProvider, client: httpx.AsyncClient) -> ProviderResult:
    """Run one provider without letting a failure escape to the caller."""
    try:
        return await provider.poll(client)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a provider must never break the pipeline
        return ProviderResult(
            provider.source.source_id, False, error=f"{type(exc).__name__}: {exc}"
        )


DEFAULT_CATEGORY_HINTS: dict[str, NewsCategory] = {
    "rbi": NewsCategory.REGULATORY,
    "sebi": NewsCategory.REGULATORY,
    "nse": NewsCategory.CORPORATE_ANNOUNCEMENT,
    "bse": NewsCategory.CORPORATE_ANNOUNCEMENT,
}
