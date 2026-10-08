"""v2: async job status, structured results, channel/engine metadata and latency metrics.

Additive only (new nullable columns and indexes), so it is safe to run against a live
database and old rows keep working. Columns are added only if missing, which makes the
migration tolerant of databases touched by development builds.

Revision ID: 0002_jobs_and_metrics
Revises: 0001_baseline
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_jobs_and_metrics"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

USER_COLUMNS = [
    sa.Column("engine_preference", sa.String(), nullable=True),
    sa.Column("claimed_at", sa.DateTime(), nullable=True),
]

INTERACTION_COLUMNS = [
    sa.Column("status", sa.String(), nullable=True),
    sa.Column("stage", sa.String(), nullable=True),
    sa.Column("title", sa.String(), nullable=True),
    sa.Column("channel", sa.String(), nullable=True),
    sa.Column("engine", sa.String(), nullable=True),
    sa.Column("source", sa.String(), nullable=True),
    sa.Column("models", sa.String(), nullable=True),
    sa.Column("external_id", sa.String(), nullable=True),
    sa.Column("asr_ms", sa.Integer(), nullable=True),
    sa.Column("llm_ms", sa.Integer(), nullable=True),
    sa.Column("total_ms", sa.Integer(), nullable=True),
    sa.Column("completed_at", sa.DateTime(), nullable=True),
]


def _add_missing_columns(table: str, columns: list[sa.Column]) -> None:
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    existing = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("feedbacks"):
        # Very early deployments predate the feedback table.
        op.create_table(
            "feedbacks",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
        )
        op.create_index("ix_feedbacks_id", "feedbacks", ["id"])

    _add_missing_columns("users", USER_COLUMNS)
    _add_missing_columns("interactions", INTERACTION_COLUMNS)

    _create_index_if_missing("ix_interactions_status", "interactions", ["status"])
    _create_index_if_missing("ix_interactions_user_ts", "interactions", ["user_id", "timestamp"])
    _create_index_if_missing("ux_interactions_external_id", "interactions", ["external_id"], unique=True)

    # Backfill what can be derived for rows written by v1.
    op.execute(
        """
        UPDATE interactions SET channel = CASE
            WHEN substr(user_id, 1, 3) = 'tg_' THEN 'telegram'
            WHEN substr(user_id, 1, 3) = 'wa_' THEN 'whatsapp'
            ELSE 'web'
        END
        WHERE channel IS NULL
        """
    )
    op.execute(
        """
        UPDATE interactions SET status = CASE
            WHEN error_message IS NOT NULL THEN 'failed'
            ELSE 'done'
        END
        WHERE status IS NULL
        """
    )


def downgrade() -> None:
    for name in ("ux_interactions_external_id", "ix_interactions_user_ts", "ix_interactions_status"):
        op.drop_index(name, table_name="interactions")
    with op.batch_alter_table("interactions") as batch:
        for column in reversed(INTERACTION_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("users") as batch:
        for column in reversed(USER_COLUMNS):
            batch.drop_column(column.name)
