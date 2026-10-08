"""Alembic environment. Migrations are run programmatically by vaani.db.migrate, which
passes an open connection; running `alembic` from the CLI is also supported via
`-x url=<DATABASE_URL>` or the DATABASE_URL environment variable."""

import os

from alembic import context
from sqlalchemy import create_engine

from vaani.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ["DATABASE_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
