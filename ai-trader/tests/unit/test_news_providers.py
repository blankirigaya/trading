"""News provider parsing, source policy, and dedup/clustering behaviour."""

import httpx
import pytest

from ai_trader.domain.news import (
    NewsCategory,
    NewsItem,
    NewsSource,
    SourceType,
    TrustLevel,
)
from ai_trader.news.clustering import NewsClusterIndex, similar_headline
from ai_trader.news.dedup import (
    canonical_url_key,
    content_hash_for,
    headline_hash_for,
    normalize_content_for_hash,
    normalize_headline,
)
from ai_trader.news.documents import (
    DocumentPolicyError,
    discover_document_urls,
    download_document,
    extract_text,
    host_allowed,
    is_trusted_source,
)
from ai_trader.news.official import OfficialListingHTMLProvider
from ai_trader.news.provider import GenericRSSProvider, canonicalize_url, clean_text
from ai_trader.news.sources import all_sources, build_provider, source_by_id

RSS_FIXTURE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>Infosys wins 5,000 crore order from global bank</title>
    <link>https://example.com/a?utm_source=rss&amp;id=7</link>
    <guid isPermaLink="false">example-a</guid>
    <pubDate>Mon, 01 Sep 2026 09:30:00 +0530</pubDate>
    <description><![CDATA[<p>Infosys said the deal will be recognised over four quarters.</p>]]></description>
  </item>
  <item>
    <title>RBI holds policy rate unchanged</title>
    <link>https://example.com/b</link>
    <guid>example-b</guid>
    <pubDate>Mon, 01 Sep 2026 11:00:00 +0530</pubDate>
    <description>Monetary policy statement published.</description>
  </item>
</channel></rss>"""

ATOM_FIXTURE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Tata Motors expands EV capacity</title>
    <link href="https://example.com/atom-1"/>
    <id>atom-1</id>
    <updated>2026-09-01T10:00:00Z</updated>
    <summary>Board approved a new plant.</summary>
  </entry>
</feed>"""


def _rss_source() -> NewsSource:
    return NewsSource(
        source_id="test-rss",
        name="Test Feed",
        homepage="https://example.com/rss",
        source_type=SourceType.NEWS_MEDIA,
        trust_level=TrustLevel.SECONDARY,
        default_category=NewsCategory.MARKET_NEWS,
    )


def _client(
    body: str, status: int = 200, content_type: str = "application/rss+xml"
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, content=body.encode(), headers={"content-type": content_type}
            )
        )
    )


# --- parsing ---------------------------------------------------------------


async def test_rss_items_are_parsed_from_channel() -> None:
    provider = GenericRSSProvider(_rss_source())
    async with _client(RSS_FIXTURE) as client:
        result = await provider.poll(client)
    assert result.ok is True
    assert result.item_count == 2
    first = result.items[0]
    assert first.headline.startswith("Infosys wins")
    assert first.source_native_id == "example-a"
    assert first.published_at.tzinfo is not None
    assert "recognised over four quarters" in (first.excerpt or "")


async def test_atom_entries_are_parsed() -> None:
    provider = GenericRSSProvider(_rss_source())
    async with _client(ATOM_FIXTURE, content_type="application/atom+xml") as client:
        result = await provider.poll(client)
    assert result.item_count == 1
    assert result.items[0].headline == "Tata Motors expands EV capacity"


async def test_malformed_feed_reports_failure_without_raising() -> None:
    provider = GenericRSSProvider(_rss_source())
    async with _client("<not-a-feed>") as client:
        result = await provider.poll(client)
    assert result.ok is False
    assert "malformed feed XML" in (result.error or "")


async def test_http_error_reports_status() -> None:
    provider = GenericRSSProvider(_rss_source())
    async with _client("", status=503) as client:
        result = await provider.poll(client)
    assert result.ok is False
    assert result.http_status == 503


async def test_official_provider_reports_when_listing_is_js_rendered() -> None:
    source = NewsSource(
        source_id="official-x",
        name="Official",
        homepage="https://example.gov.in/list",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
    )
    provider = OfficialListingHTMLProvider(source)
    body = "<html><body><a href='/x'>Home</a></body></html>"
    async with _client(body, content_type="text/html") as client:
        result = await provider.poll(client)
    assert result.item_count == 0
    assert "JavaScript-rendered" in (result.error or "")


async def test_official_provider_parses_dated_announcement() -> None:
    source = NewsSource(
        source_id="official-y",
        name="Official",
        homepage="https://example.gov.in/list",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
        default_category=NewsCategory.REGULATORY,
    )
    provider = OfficialListingHTMLProvider(source)
    body = (
        "<html><body><ul>"
        '<li><a href="/pr/2026/sebi-circular-on-disclosure-requirements.htm">'
        "01 Sep 2026 - SEBI issues circular on disclosure requirements for listed entities</a></li>"
        "</ul></body></html>"
    )
    async with _client(body, content_type="text/html") as client:
        result = await provider.poll(client)
    assert result.item_count == 1
    item = result.items[0]
    assert item.trust_level is TrustLevel.PRIMARY
    assert "01 Sep 2026" not in item.headline
    assert "SEBI issues circular" in item.headline
    assert item.category is NewsCategory.REGULATORY


# --- source policy ---------------------------------------------------------


def test_source_registry_covers_required_official_sources() -> None:
    ids = {source.source_id for source in all_sources()}
    assert {"nse-corporate", "rbi-press-releases", "sebi-news", "bse-corporate"} <= ids


def test_official_sources_are_primary_and_markets_are_secondary() -> None:
    for source in all_sources():
        if source.source_type is SourceType.OFFICIAL:
            assert source.trust_level is TrustLevel.PRIMARY
        if source.source_type is SourceType.NEWS_MEDIA:
            assert source.trust_level is TrustLevel.SECONDARY


def test_source_policy_never_uses_undocumented_apis() -> None:
    for source in all_sources():
        assert "/api/" not in source.homepage, f"{source.source_id} uses a private API"


def test_provider_selection_matches_source_type() -> None:
    assert isinstance(
        build_provider(source_by_id("rbi-press-releases")), OfficialListingHTMLProvider
    )
    assert isinstance(build_provider(source_by_id("livemint-markets")), GenericRSSProvider)


def test_disabled_sources_are_not_enabled() -> None:
    from ai_trader.news.sources import enabled_sources

    enabled = {source.source_id for source in enabled_sources()}
    assert "rss-configured-1" not in enabled


# --- dedup -----------------------------------------------------------------


def test_canonical_url_drops_tracking_and_fragment() -> None:
    assert (
        canonicalize_url("https://example.com/a?utm_source=x&id=7#frag")
        == "https://example.com/a?id=7"
    )
    assert canonical_url_key("https://www.example.com/a/") == "example.com/a"


def test_headline_normalization_ignores_case_punctuation_stopwords() -> None:
    left = normalize_headline("Infosys wins a big 5,000 crore order!")
    right = normalize_headline("Infosys wins big 5,000 crore order")
    assert left == right
    assert headline_hash_for("Infosys wins a big order") == headline_hash_for(
        "infosys wins big order"
    )


def test_different_headlines_hash_differently() -> None:
    assert headline_hash_for("RBI holds rate") != headline_hash_for("RBI cuts rate")
    assert content_hash_for("body one") != content_hash_for("body two")


def test_normalize_content_strips_volatile_fragments() -> None:
    base = "Infosys said the deal will be recognised over four quarters."
    assert normalize_content_for_hash(base) == normalize_content_for_hash(
        "Infosys said the deal will be recognised over four quarters. Updated at 10:47 IST."
    )
    assert normalize_content_for_hash("Markets rallied 2% in afternoon trade.") == (
        normalize_content_for_hash("Markets rallied 2% in afternoon trade. 3 hours ago")
    )
    assert normalize_content_for_hash("") == ""
    assert normalize_content_for_hash(None) == ""


def test_normalize_content_keeps_material_changes() -> None:
    before = "Infosys said the deal will be recognised over four quarters."
    after = "Infosys cancelled the deal citing regulatory concerns."
    assert normalize_content_for_hash(before) != normalize_content_for_hash(after)


# --- clustering ------------------------------------------------------------


def _item(
    item_id: str,
    headline: str,
    trust: TrustLevel = TrustLevel.SECONDARY,
    source_type: SourceType = SourceType.NEWS_MEDIA,
) -> NewsItem:
    from datetime import UTC, datetime

    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    return NewsItem(
        item_id=item_id,
        source_id=f"src-{item_id}",
        source_name=f"Source {item_id}",
        source_type=source_type,
        trust_level=trust,
        headline=headline,
        canonical_url=f"https://example.com/{item_id}",
        url=f"https://example.com/{item_id}",
        published_at=now,
        received_at=now,
        category=NewsCategory.MARKET_NEWS,
        content_hash=content_hash_for(item_id),
        headline_hash=headline_hash_for(headline),
    )


def test_same_url_from_two_sources_clusters_and_preserves_both() -> None:
    index = NewsClusterIndex()
    first = _item("a", "Infosys wins 5000 crore order")
    second = first.model_copy(
        update={
            "item_id": "b",
            "source_id": "other",
            "source_name": "Other outlet",
            "trust_level": TrustLevel.PRIMARY,
        }
    )
    cluster_a, dup_a = index.add(first, first.received_at)
    cluster_b, dup_b = index.add(second, second.received_at)

    assert dup_a is False
    assert dup_b is True
    assert cluster_a.cluster_id == cluster_b.cluster_id
    assert set(cluster_b.member_item_ids) == {"a", "b"}
    # The higher-trust source becomes representative without dropping the original.
    assert cluster_b.representative_item_id == "b"
    assert cluster_b.highest_trust_level is TrustLevel.PRIMARY
    assert cluster_b.duplicate_count == 1


def test_syndicated_rewrite_clusters_via_similarity() -> None:
    index = NewsClusterIndex()
    index.add(
        _item("a", "Infosys wins 5,000 crore order from global bank"), _item("a", "x").received_at
    )
    cluster, is_duplicate = index.add(
        _item("b", "Global bank hands Infosys 5,000 crore contract"), _item("b", "x").received_at
    )
    assert is_duplicate is True
    assert len(cluster.member_item_ids) == 2


def test_unrelated_stories_stay_separate() -> None:
    index = NewsClusterIndex()
    index.add(_item("a", "Infosys wins 5000 crore order"), _item("a", "x").received_at)
    cluster, is_duplicate = index.add(
        _item("b", "RBI keeps policy rate unchanged"), _item("b", "x").received_at
    )
    assert is_duplicate is False
    assert len(cluster.member_item_ids) == 1


def test_similar_headline_helper() -> None:
    assert similar_headline("Infosys wins 5,000 crore order", "Infosys wins 5,000-crore order")
    assert not similar_headline("Infosys wins order", "RBI holds rates")


def test_rebuild_is_deterministic() -> None:
    items = [
        _item("a", "Infosys wins 5000 crore order"),
        _item("b", "Infosys wins 5000 crore order"),
        _item("c", "RBI holds policy rate unchanged"),
    ]
    first = NewsClusterIndex()
    first.rebuild(items)
    second = NewsClusterIndex()
    second.rebuild(items)
    assert sorted(first.cluster_ids()) == sorted(second.cluster_ids())
    # a and b share a canonical URL shape and headline, so they form one cluster.
    assert len(first.cluster_ids()) == 2


# --- documents -------------------------------------------------------------


def test_documents_only_for_trusted_sources() -> None:
    official = _item("a", "x", TrustLevel.PRIMARY, SourceType.OFFICIAL)
    company = _item("b", "x", TrustLevel.HIGH, SourceType.COMPANY)
    media = _item("c", "x", TrustLevel.SECONDARY, SourceType.NEWS_MEDIA)
    # A high trust level alone is not enough: the source type must be official/company.
    mislabelled = _item("d", "x", TrustLevel.PRIMARY, SourceType.NEWS_MEDIA)
    assert is_trusted_source(official) is True
    assert is_trusted_source(company) is True
    assert is_trusted_source(media) is False
    assert is_trusted_source(mislabelled) is False


def test_document_allowlist() -> None:
    allowed = ("sebi.gov.in", "www.sebi.gov.in")
    assert host_allowed("https://sebi.gov.in/x.pdf", allowed) is True
    assert host_allowed("https://SEBI.GOV.IN/x.pdf", allowed) is True
    assert host_allowed("https://evil.example/x.pdf", allowed) is False
    assert host_allowed("https://sebi.gov.in.evil.com/x.pdf", allowed) is False
    assert host_allowed("https://sebi.gov.in/x.pdf", ()) is False


def test_discovery_ignores_untrusted_and_offsite_links() -> None:
    trusted = _item("a", "x", TrustLevel.PRIMARY, SourceType.OFFICIAL)
    html = (
        '<a href="https://sebi.gov.in/circular.pdf">circular</a>'
        '<a href="https://evil.example/payload.pdf">prompt injection</a>'
        '<a href="https://sebi.gov.in/page.html">not a pdf</a>'
    )
    urls = discover_document_urls(trusted, html, ("sebi.gov.in",))
    assert urls == ["https://sebi.gov.in/circular.pdf"]

    untrusted = _item("b", "x", TrustLevel.SECONDARY, SourceType.NEWS_MEDIA)
    assert discover_document_urls(untrusted, html, ("sebi.gov.in",)) == []


async def test_download_refuses_non_allowlisted_host(tmp_path) -> None:
    with pytest.raises(DocumentPolicyError, match="not on the allowlist"):
        await download_document(
            httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            "https://evil.example/x.pdf",
            ("sebi.gov.in",),
            tmp_path,
        )


async def test_download_records_hash_and_extracts_text(tmp_path) -> None:
    html = b"<html><head><title>SEBI Circular</title></head><body><p>Disclosure rules.</p></body></html>"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=html, headers={"content-type": "text/html"})
        )
    )
    document = await download_document(
        client, "https://sebi.gov.in/x.htm", ("sebi.gov.in",), tmp_path
    )
    await client.aclose()
    assert len(document.sha256) == 64
    assert document.sha256 == document.sha256.lower()
    assert "SEBI Circular" in (document.extracted_text or "")
    assert document.local_path is not None
    assert (tmp_path / f"{document.document_id}.html").exists()


async def test_download_refuses_oversized_document(tmp_path) -> None:
    from ai_trader.news import documents as docs

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (docs.MAX_DOCUMENT_BYTES + 10))
        )
    )
    with pytest.raises(DocumentPolicyError, match="maximum permitted size"):
        await download_document(client, "https://sebi.gov.in/big.pdf", ("sebi.gov.in",), tmp_path)
    await client.aclose()


def test_extract_text_handles_html_and_pdf() -> None:
    text, truncated = extract_text(
        b"<html><body><p>Hello <b>world</b></p></body></html>", "text/html"
    )
    assert "Hello world" in text
    assert truncated is False
    pdf_text, pdf_truncated = extract_text(b"%PDF-1.4 binary", "application/pdf")
    assert pdf_text == ""
    assert pdf_truncated is True


def test_clean_text_strips_markup() -> None:
    assert clean_text("<p>Hello   <b>world</b></p>") == "Hello world"
    assert clean_text(None) == ""
