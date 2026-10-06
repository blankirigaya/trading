"""Group duplicate reports of one story into a `NewsEventCluster`.

Clustering never discards a source: every distinct item becomes a member, and the
highest-trust source is chosen as the representative.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ai_trader.domain.news import NewsEventCluster, NewsItem, TrustLevel
from ai_trader.news.dedup import (
    canonical_url_key,
    content_hash_for,
    dedup_keys,
    headline_hash_for,
    headline_similarity_key,
    normalize_headline,
)

_TRUST_ORDER = {
    TrustLevel.PRIMARY: 3,
    TrustLevel.HIGH: 2,
    TrustLevel.SECONDARY: 1,
    TrustLevel.UNKNOWN: 0,
}

# Significant tokens used to decide whether two headlines describe one event.
_STOP = frozenset({"nse", "bse", "rbi", "sebi", "says", "said", "news", "update", "report"})


@dataclass
class _ClusterRecord:
    """Mutable working state for one cluster."""

    headline: str
    representative_item_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    trust_level: TrustLevel
    members: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    symbols: set[str] = field(default_factory=set)


def _significant_tokens(headline: str) -> set[str]:
    return {token for token in normalize_headline(headline).split() if token not in _STOP}


def similar_headline(left: str, right: str, threshold: float = 0.6) -> bool:
    """Jaccard overlap of significant tokens. Deterministic, no ML."""
    a = _significant_tokens(left)
    b = _significant_tokens(right)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    return overlap >= threshold


class NewsClusterIndex:
    """In-memory index that assigns items to clusters.

    Duplicate detection is layered: exact identity keys first (cheap and certain),
    then headline similarity (catches syndicated rewrites).
    """

    def __init__(self, similarity_threshold: float = 0.6) -> None:
        self.similarity_threshold = similarity_threshold
        self._identity_to_cluster: dict[str, str] = {}
        self._clusters: dict[str, _ClusterRecord] = {}
        self._keys_of_item: dict[str, set[str]] = {}
        self.duplicate_item_ids: set[str] = set()

    def __len__(self) -> int:
        return len(self._clusters)

    def _item_keys(self, item: NewsItem) -> set[str]:
        keys = dedup_keys(
            item.source_id,
            item.source_native_id,
            item.canonical_url,
            item.content_hash,
            item.headline_hash,
        )
        return {f"{name}:{value}" for name, value in keys.items()}

    def existing_cluster_for(self, item: NewsItem) -> str | None:
        keys = self._item_keys(item)
        for key in keys:
            cluster_id = self._identity_to_cluster.get(key)
            if cluster_id is not None:
                return cluster_id
        for cluster_id, record in self._clusters.items():
            if similar_headline(item.headline, record.headline, self.similarity_threshold):
                return cluster_id
            for member_headline in record.headlines:
                if similar_headline(item.headline, member_headline, self.similarity_threshold):
                    return cluster_id
        return None

    def add(self, item: NewsItem, now: datetime) -> tuple[NewsEventCluster, bool]:
        """Add an item. Returns ``(cluster, is_duplicate)``.

        ``is_duplicate`` is True when an identical story was already seen; the item
        is still recorded as a member so provenance is preserved.
        """
        keys = self._item_keys(item)
        cluster_id = self.existing_cluster_for(item)
        is_duplicate = cluster_id is not None

        if cluster_id is None:
            cluster_id = f"cluster-{content_hash_for(item.canonical_url + item.headline)[:24]}"
            self._clusters[cluster_id] = _ClusterRecord(
                headline=item.headline,
                representative_item_id=item.item_id,
                first_seen_at=item.published_at,
                last_seen_at=item.published_at,
                trust_level=item.trust_level,
            )

        record = self._clusters[cluster_id]
        if item.item_id not in record.members:
            record.members.append(item.item_id)
        if item.headline not in record.headlines:
            record.headlines.append(item.headline)
        record.last_seen_at = max(record.last_seen_at, item.published_at)
        record.first_seen_at = min(record.first_seen_at, item.published_at)
        record.symbols |= set(item.symbols)
        if _TRUST_ORDER[item.trust_level] > _TRUST_ORDER[record.trust_level]:
            record.trust_level = item.trust_level
            # A higher-trust source becomes the representative, keeping the original
            # item intact in the member list.
            record.representative_item_id = item.item_id

        for key in keys:
            self._identity_to_cluster[key] = cluster_id
        self._keys_of_item[item.item_id] = keys
        if is_duplicate:
            self.duplicate_item_ids.add(item.item_id)

        return self._build(cluster_id, record), is_duplicate

    def _build(self, cluster_id: str, record: _ClusterRecord) -> NewsEventCluster:
        members = tuple(record.members)
        return NewsEventCluster(
            cluster_id=cluster_id,
            canonical_headline=record.headline,
            representative_item_id=record.representative_item_id,
            member_item_ids=members,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
            symbols=tuple(sorted(record.symbols)),
            highest_trust_level=record.trust_level,
            duplicate_count=max(0, len(members) - 1),
        )

    def cluster_ids(self) -> Iterable[str]:
        return self._clusters.keys()

    def rebuild(self, items: Iterable[NewsItem]) -> None:
        """Deterministically rebuild the index, useful for replay and tests."""
        self.__init__(self.similarity_threshold)  # type: ignore[misc]
        for item in sorted(items, key=lambda entry: (entry.published_at, entry.item_id)):
            self.add(item, item.received_at)


def cluster_from_items(
    items: list[NewsItem], threshold: float = 0.6
) -> dict[str, NewsEventCluster]:
    """Convenience builder used by tests and one-shot commands."""
    index = NewsClusterIndex(threshold)
    latest = max((item.received_at for item in items), default=None)
    clusters: dict[str, NewsEventCluster] = {}
    for item in items:
        cluster, _ = index.add(item, latest or item.received_at)
        clusters[cluster.cluster_id] = cluster
    return clusters


__all__ = [
    "NewsClusterIndex",
    "canonical_url_key",
    "cluster_from_items",
    "headline_hash_for",
    "headline_similarity_key",
    "similar_headline",
]
