"""Baseline: the schema v1 created with Base.metadata.create_all().

Existing databases already have these tables; vaani.db.migrate stamps them at this
revision instead of running it.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])

    op.create_table(
        "interactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("audio_duration_sec", sa.Float(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("action_items", sa.Text(), nullable=True),
        sa.Column("sentiment", sa.String(), nullable=True),
        sa.Column("accuracy_rating", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_interactions_id", "interactions", ["id"])

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_feedbacks_id", "feedbacks", ["id"])


def downgrade() -> None:
    op.drop_table("feedbacks")
    op.drop_table("interactions")
    op.drop_table("users")
