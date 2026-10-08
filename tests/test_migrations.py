"""Migrations must adopt databases created by v1 (create_all, no alembic) without data loss."""

from sqlalchemy import create_engine, inspect, text

from vaani.db.migrate import run_migrations
from vaani.db.models import Interaction

# Exactly what v1's Base.metadata.create_all() produced.
V1_SCHEMA = [
    "CREATE TABLE users (id VARCHAR NOT NULL, tier VARCHAR, created_at DATETIME, PRIMARY KEY (id))",
    "CREATE INDEX ix_users_id ON users (id)",
    """CREATE TABLE interactions (id VARCHAR NOT NULL, user_id VARCHAR, timestamp DATETIME,
        audio_duration_sec FLOAT, transcript TEXT, summary TEXT, action_items TEXT, sentiment VARCHAR,
        accuracy_rating VARCHAR, error_message TEXT, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id))""",
    "CREATE INDEX ix_interactions_id ON interactions (id)",
    """CREATE TABLE feedbacks (id VARCHAR NOT NULL, user_id VARCHAR, timestamp DATETIME, message TEXT NOT NULL,
        PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id))""",
    "CREATE INDEX ix_feedbacks_id ON feedbacks (id)",
]


def test_fresh_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    run_migrations(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"users", "interactions", "feedbacks", "alembic_version"} <= tables
    model_columns = {c.name for c in Interaction.__table__.columns}
    assert model_columns <= {c["name"] for c in inspect(engine).get_columns("interactions")}


def test_adopts_v1_database_and_backfills(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'v1.db'}")
    with engine.begin() as conn:
        for statement in V1_SCHEMA:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO users (id, tier) VALUES ('tg_42', 'free'), ('user_abc12', 'free')"))
        conn.execute(
            text(
                "INSERT INTO interactions (id, user_id, summary, action_items, sentiment) "
                "VALUES ('ok', 'tg_42', 'A summary', '[\"Old style string item\"]', 'Neutral')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO interactions (id, user_id, error_message, sentiment) "
                "VALUES ('bad', 'user_abc12', 'boom', 'Error')"
            )
        )

    run_migrations(engine)
    run_migrations(engine)  # idempotent

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, channel, status, summary FROM interactions ORDER BY id")).fetchall()
        assert rows == [("bad", "web", "failed", None), ("ok", "telegram", "done", "A summary")]
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002_jobs_and_metrics"


def test_v1_string_action_items_still_render():
    note = Interaction(action_items='["Call Rahul", {"task": "Send deck", "owner": "Me", "due": "Friday"}]')
    assert note.action_items_list() == [
        {"task": "Call Rahul", "owner": None, "due": None},
        {"task": "Send deck", "owner": "Me", "due": "Friday"},
    ]
    assert Interaction(action_items="not json").action_items_list() == []
