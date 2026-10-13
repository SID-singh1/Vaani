"""Engagement events and the Pro waitlist.

Additive only (two new tables), so it is safe to run against a live database.

Revision ID: 0003_events_and_waitlist
Revises: 0002_jobs_and_metrics
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_events_and_waitlist"
down_revision = "0002_jobs_and_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("events"):
        op.create_table(
            "events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("note_id", sa.String(), nullable=True),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("channel", sa.String(), nullable=True),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_events_type_ts", "events", ["type", "timestamp"])
    if not inspector.has_table("waitlist"):
        op.create_table(
            "waitlist",
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("channel", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("waitlist")
    op.drop_index("ix_events_type_ts", table_name="events")
    op.drop_table("events")
