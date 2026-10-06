"""Phase 2B follow-up: continuous ingestion support.

Adds correction/version tracking for news items and the end-to-end latency
timestamps needed to measure news-to-trade delay.

Revision ID: 0004_news_realtime
Revises: 0003_news_intelligence
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_news_realtime"
down_revision = "0003_news_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A publisher that revises a filing or article produces a new row rather than
    # mutating the old one, so the original evidence stays auditable.
    op.add_column(
        "news_items",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "news_items",
        sa.Column("supersedes_item_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_news_items_supersedes", "news_items", ["supersedes_item_id"])
    # Identity of an item at its source, used to spot a revision of the same item.
    op.create_index("ix_news_items_source_identity", "news_items", ["source_id", "canonical_url"])

    # Latency chain: news received -> analysis -> review started -> decision -> paper order.
    op.add_column(
        "news_analyses",
        sa.Column("portfolio_review_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "news_analyses",
        sa.Column("portfolio_decision_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "news_analyses",
        sa.Column("paper_order_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "news_analyses",
        sa.Column("news_decision_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("news_analyses", "news_decision_id")
    op.drop_column("news_analyses", "paper_order_at")
    op.drop_column("news_analyses", "portfolio_decision_at")
    op.drop_column("news_analyses", "portfolio_review_started_at")
    op.drop_index("ix_news_items_source_identity", table_name="news_items")
    op.drop_index("ix_news_items_supersedes", table_name="news_items")
    op.drop_column("news_items", "supersedes_item_id")
    op.drop_column("news_items", "version")
