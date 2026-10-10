"""Run Alembic migrations at startup, adopting databases created by v1.

v1 created tables with metadata.create_all() and never recorded a revision. Such a
database is stamped at the baseline revision (its schema matches exactly) and then
upgraded normally, so production data is kept and only additive changes are applied.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
BASELINE = "0001_baseline"


def _config(connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


def run_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        cfg = _config(connection)
        if "alembic_version" not in tables and "interactions" in tables:
            log.info("Adopting pre-migration database: stamping %s", BASELINE)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
    log.info("Database schema is up to date")
