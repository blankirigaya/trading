"""Phase 2B: news intelligence tables.

Revision ID: 0003_news_intelligence
Revises: 0002_shadow_provenance
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_news_intelligence"
down_revision = "0002_shadow_provenance"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "news_sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("homepage", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("trust_level", sa.String(32), nullable=False),
        sa.Column("default_category", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("allowed_document_hosts", JSONType, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_news_sources_source_type", "news_sources", ["source_type"])
    op.create_index("ix_news_sources_trust_level", "news_sources", ["trust_level"])

    op.create_table(
        "news_items",
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("trust_level", sa.String(32), nullable=False),
        sa.Column("source_native_id", sa.String(200), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("headline_hash", sa.String(64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("raw_metadata", JSONType, nullable=True),
        sa.Column("cluster_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_news_items_source_id", "news_items", ["source_id"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_received_at", "news_items", ["received_at"])
    op.create_index("ix_news_items_category", "news_items", ["category"])
    op.create_index("ix_news_items_trust_level", "news_items", ["trust_level"])
    op.create_index("ix_news_items_content_hash", "news_items", ["content_hash"])
    op.create_index("ix_news_items_headline_hash", "news_items", ["headline_hash"])
    op.create_index("ix_news_items_cluster_id", "news_items", ["cluster_id"])
    op.create_index("ix_news_items_source_published", "news_items", ["source_id", "published_at"])

    op.create_table(
        "news_event_clusters",
        sa.Column("cluster_id", sa.String(64), primary_key=True),
        sa.Column("canonical_headline", sa.Text(), nullable=False),
        sa.Column("representative_item_id", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols", JSONType, nullable=True),
        sa.Column("highest_trust_level", sa.String(32), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "news_cluster_members",
        sa.Column("cluster_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_representative", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_news_cluster_members_source_id", "news_cluster_members", ["source_id"])

    op.create_table(
        "news_documents",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("text_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_news_documents_item_id", "news_documents", ["item_id"])
    op.create_index("ix_news_documents_sha256", "news_documents", ["sha256"])

    op.create_table(
        "news_analyses",
        sa.Column("analysis_id", sa.String(64), primary_key=True),
        sa.Column("cluster_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("affected_symbols", JSONType, nullable=True),
        sa.Column("materiality", sa.String(32), nullable=True),
        sa.Column("direction", sa.String(32), nullable=True),
        sa.Column("horizon", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "requires_portfolio_review", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("evidence", JSONType, nullable=True),
        sa.Column("uncertainties", JSONType, nullable=True),
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analysis_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("portfolio_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("provider_metadata", JSONType, nullable=True),
    )
    op.create_index("ix_news_analyses_cluster_id", "news_analyses", ["cluster_id"])
    op.create_index("ix_news_analyses_item_id", "news_analyses", ["item_id"])
    op.create_index("ix_news_analyses_status", "news_analyses", ["status"])
    op.create_index("ix_news_analyses_materiality", "news_analyses", ["materiality"])
    op.create_index("ix_news_analyses_direction", "news_analyses", ["direction"])
    op.create_index(
        "ix_news_analyses_requires_review", "news_analyses", ["requires_portfolio_review"]
    )

    op.create_table(
        "news_symbol_links",
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(40), primary_key=True),
        sa.Column("link_type", sa.String(40), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_news_symbol_links_symbol", "news_symbol_links", ["symbol"])


def downgrade() -> None:
    for table in (
        "news_symbol_links",
        "news_analyses",
        "news_documents",
        "news_cluster_members",
        "news_event_clusters",
        "news_items",
        "news_sources",
    ):
        op.drop_table(table)
