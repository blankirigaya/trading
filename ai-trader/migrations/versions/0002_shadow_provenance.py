"""Phase 2A: live-shadow decision provenance columns.

Revision ID: 0002_shadow_provenance
Revises: 0001_phase1
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_shadow_provenance"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("ai_decisions", sa.Column("jev_critique", JSONType, nullable=True))
    op.add_column("ai_decisions", sa.Column("jev_provider_metadata", JSONType, nullable=True))
    op.add_column("ai_decisions", sa.Column("orders_generated", JSONType, nullable=True))
    op.add_column("ai_decisions", sa.Column("portfolio_before", JSONType, nullable=True))
    op.add_column("ai_decisions", sa.Column("portfolio_after", JSONType, nullable=True))
    op.add_column("ai_decisions", sa.Column("symbol", sa.String(40), nullable=True))
    op.add_column("ai_decisions", sa.Column("instrument_key", sa.String(80), nullable=True))
    op.add_column("ai_decisions", sa.Column("market_snapshot_id", sa.String(64), nullable=True))
    op.add_column(
        "ai_decisions", sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("ai_decisions", sa.Column("ai_server", sa.String(160), nullable=True))
    op.add_column("ai_decisions", sa.Column("ai_model", sa.String(120), nullable=True))
    op.add_column("ai_decisions", sa.Column("ai_latency_ms", sa.Integer(), nullable=True))
    op.add_column("ai_decisions", sa.Column("jev_model", sa.String(120), nullable=True))
    op.add_column("ai_decisions", sa.Column("jev_latency_ms", sa.Integer(), nullable=True))
    op.add_column("ai_decisions", sa.Column("prompt_name", sa.String(80), nullable=True))
    op.add_column("ai_decisions", sa.Column("prompt_version", sa.String(40), nullable=True))
    op.add_column(
        "ai_decisions",
        sa.Column(
            "reconsideration_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "ai_decisions",
        sa.Column(
            "reconsideration_performed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("ai_decisions", sa.Column("safety_allowed", sa.Boolean(), nullable=True))
    op.add_column("ai_decisions", sa.Column("safety_reason", sa.String(200), nullable=True))
    op.add_column("ai_decisions", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.create_index("ix_ai_decisions_symbol", "ai_decisions", ["symbol"])
    op.create_index("ix_ai_decisions_market_snapshot_id", "ai_decisions", ["market_snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_decisions_market_snapshot_id", table_name="ai_decisions")
    op.drop_index("ix_ai_decisions_symbol", table_name="ai_decisions")
    for column in (
        "failure_reason",
        "safety_reason",
        "safety_allowed",
        "reconsideration_performed",
        "reconsideration_requested",
        "prompt_version",
        "prompt_name",
        "jev_latency_ms",
        "jev_model",
        "ai_latency_ms",
        "ai_model",
        "ai_server",
        "market_timestamp",
        "market_snapshot_id",
        "instrument_key",
        "symbol",
        "portfolio_after",
        "portfolio_before",
        "orders_generated",
        "jev_provider_metadata",
        "jev_critique",
    ):
        op.drop_column("ai_decisions", column)
