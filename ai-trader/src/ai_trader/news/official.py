"""Official regulator and exchange listings.

These are public web pages. NSE's private JSON endpoints are deliberately NOT used:
they are undocumented and return 403 to non-browser clients. When an official site
is JavaScript-rendered or blocks us, the provider reports that honestly rather than
pretending to have parsed an item.
"""

import re
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx

from ai_trader.domain.news import (
    NewsCategory,
    NewsItem,
    NewsSource,
    SourceType,
    TrustLevel,
)
from ai_trader.news.dedup import content_hash_for, headline_hash_for
from ai_trader.news.provider import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    NewsProvider,
    ProviderResult,
    clean_text,
)

_ANCHOR_RE = re.compile(
    r'<a[^>]+href\s*=\s*["\'](?P<href>[^"\']+)["\'][^>]*>(?P<text>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DATE_IN_TEXT_RE = re.compile(
    r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})",
    re.IGNORECASE,
)
_MIN_HEADLINE_CHARS = 18
# Link text that is navigation chrome rather than an announcement.
_NOISE_RE = re.compile(
    r"^(home|about|contact|login|search|skip|menu|more|next|previous)$", re.IGNORECASE
)


class OfficialListingHTMLProvider(NewsProvider):
    """Extracts announcement links from an official listing page.

    Deliberately generic: official sites change markup often, so the provider
    collects dated anchor text from configured hosts only.
    """

    def __init__(self, source: NewsSource, timeout: int = DEFAULT_TIMEOUT) -> None:
        super().__init__(source, timeout=timeout)

    async def poll(self, client: httpx.AsyncClient) -> ProviderResult:
        try:
            response = await client.get(
                self.source.homepage,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            )
        except httpx.HTTPError as exc:
            return ProviderResult(
                self.source.source_id, False, error=f"request failed: {type(exc).__name__}"
            )
        if response.status_code >= 400:
            return ProviderResult(
                self.source.source_id,
                False,
                error=f"upstream returned HTTP {response.status_code}",
                http_status=response.status_code,
            )

        received_at = datetime.now(UTC)
        items = self._extract(response.text, received_at)
        note = None
        if not items:
            note = (
                "listing page returned no dated announcement anchors; the site is "
                "JavaScript-rendered or uses an undocumented API"
            )
        return ProviderResult(
            self.source.source_id, bool(items), items, error=note, http_status=200
        )

    def _extract(self, html: str, received_at: datetime) -> tuple[NewsItem, ...]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        for match in _ANCHOR_RE.finditer(html):
            headline = clean_text(match.group("text"))
            href = match.group("href").strip()
            if len(headline) < _MIN_HEADLINE_CHARS or _NOISE_RE.match(headline):
                continue
            date_match = _DATE_IN_TEXT_RE.search(headline)
            if not date_match:
                continue
            absolute = urljoin(self.source.homepage, href)
            if absolute in seen:
                continue
            seen.add(absolute)

            published = _parse_human_date(date_match.group(1), received_at)
            items.append(
                NewsItem(
                    item_id=content_hash_for(f"{self.source.source_id}|{absolute}")[:32],
                    source_id=self.source.source_id,
                    source_name=self.source.name,
                    source_type=self.source.source_type,
                    trust_level=self.source.trust_level,
                    headline=re.sub(r"^\d{1,2}\s+\w{3}\s+\d{4}\s*[–-]?\s*", "", headline).strip()
                    or headline,
                    canonical_url=absolute,
                    url=absolute,
                    published_at=published,
                    received_at=received_at,
                    category=self.source.default_category,
                    content_hash=content_hash_for(headline),
                    headline_hash=headline_hash_for(headline),
                    excerpt=headline[:600],
                    raw_metadata={"listing_page": self.source.homepage},
                )
            )
        return tuple(items[:40])


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_human_date(text: str, fallback: datetime) -> datetime:
    parts = text.split()
    if len(parts) < 3:
        return fallback
    try:
        day = int(parts[0])
        month = _MONTHS.get(parts[1][:3].lower())
        year = int(parts[2])
    except (ValueError, TypeError):
        return fallback
    if month is None:
        return fallback
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return fallback


# --- Source registry -------------------------------------------------------
# Trust levels are configuration, not AI output. Only official domains listed here
# are ever used for official sources.


def official_sources() -> list[NewsSource]:
    return [
        NewsSource(
            source_id="nse-corporate",
            name="NSE India (corporate announcements)",
            homepage="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            source_type=SourceType.OFFICIAL,
            trust_level=TrustLevel.PRIMARY,
            default_category=NewsCategory.CORPORATE_ANNOUNCEMENT,
            allowed_document_hosts=("nsearchives.nseindia.com", "nseindia.com"),
            notes="Public listing page only. Undocumented JSON APIs are not used.",
        ),
        NewsSource(
            source_id="rbi-press-releases",
            name="Reserve Bank of India (press releases)",
            homepage="https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx",
            source_type=SourceType.OFFICIAL,
            trust_level=TrustLevel.PRIMARY,
            default_category=NewsCategory.REGULATORY,
            allowed_document_hosts=("rbi.org.in", "website.rbi.org.in", "rbidocs.rbi.org.in"),
        ),
        NewsSource(
            source_id="sebi-news",
            name="SEBI (news and circulars)",
            homepage="https://www.sebi.gov.in/sebiweb/home/HomeAction.do?homeAction=yes",
            source_type=SourceType.OFFICIAL,
            trust_level=TrustLevel.PRIMARY,
            default_category=NewsCategory.REGULATORY,
            allowed_document_hosts=("sebi.gov.in",),
        ),
        NewsSource(
            source_id="bse-corporate",
            name="BSE India (corporate announcements)",
            homepage="https://www.bseindia.com/corporates/ann.html",
            source_type=SourceType.OFFICIAL,
            trust_level=TrustLevel.PRIMARY,
            default_category=NewsCategory.CORPORATE_ANNOUNCEMENT,
            allowed_document_hosts=("bseindia.com", "pii.bseindia.com"),
        ),
    ]
