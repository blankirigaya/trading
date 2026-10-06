"""Phase 1 schema.

Revision ID: 0001_phase1
Revises:
Create Date: 2026-10-01

This migration declares its schema explicitly rather than reflecting live ORM metadata,
so later incremental migrations apply cleanly.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONType, nullable=False),
    )
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])

    op.create_table(
        "ai_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_context_hash", sa.String(64), nullable=False),
        sa.Column("initial_proposal", JSONType, nullable=False),
        sa.Column("final_decision", JSONType, nullable=True),
        sa.Column("provider_metadata", JSONType, nullable=False),
    )
    op.create_index("ix_ai_decisions_correlation_id", "ai_decisions", ["correlation_id"])
    op.create_index("ix_ai_decisions_event_id", "ai_decisions", ["event_id"])
    op.create_index("ix_ai_decisions_status", "ai_decisions", ["status"])
    op.create_index("ix_ai_decisions_timestamp", "ai_decisions", ["timestamp"])

    op.create_table(
        "ai_critiques",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("critique", JSONType, nullable=False),
        sa.Column("provider_metadata", JSONType, nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("broker_order_id", sa.String(64), primary_key=True),
        sa.Column("client_order_id", sa.String(128), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
    )
    op.create_index("ix_orders_client_order_id", "orders", ["client_order_id"])
    op.create_index("ix_orders_decision_id", "orders", ["decision_id"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "fills",
        sa.Column("fill_id", sa.String(64), primary_key=True),
        sa.Column("broker_order_id", sa.String(64), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
    )
    op.create_index("ix_fills_broker_order_id", "fills", ["broker_order_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_entry_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_snapshot_at", "positions", ["snapshot_at"])

    for table in ("news", "announcements", "portfolio_snapshots", "decision_outcomes"):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payload", JSONType, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])

    op.create_table(
        "company_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payload", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_company_memory_created_at", "company_memory", ["created_at"])


def downgrade() -> None:
    for table in (
        "company_memory",
        "decision_outcomes",
        "portfolio_snapshots",
        "announcements",
        "news",
        "positions",
        "fills",
        "orders",
        "ai_critiques",
        "ai_decisions",
        "events",
    ):
        op.drop_table(table)
