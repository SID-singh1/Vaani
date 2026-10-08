from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")


class Database:
    """Owns the SQLAlchemy engine. Sync sessions; async callers go through `run()`,
    which executes the work in a thread so database round-trips (Postgres over the
    network in production) never block the event loop."""

    def __init__(self, url: str):
        self.url = url
        self.engine: Engine = _make_engine(url)
        self._sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        def work() -> T:
            with self.session() as session:
                return fn(session, *args, **kwargs)

        return await asyncio.to_thread(work)

    def dispose(self) -> None:
        self.engine.dispose()


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    # pool_pre_ping survives Supabase/Postgres dropping idle connections on free tiers.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=1800)
