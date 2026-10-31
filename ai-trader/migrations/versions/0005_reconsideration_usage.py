"""Phase 2B: persist reconsideration inference usage for the AI cost counter.

Revision ID: 0005_reconsideration_usage
Revises: 0004_news_realtime
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_reconsideration_usage"
down_revision = "0004_news_realtime"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "ai_decisions", sa.Column("reconsideration_provider_metadata", JSONType, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_decisions", "reconsideration_provider_metadata")
