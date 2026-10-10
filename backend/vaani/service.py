"""The one entry point every channel (web, Telegram, WhatsApp) uses to create notes."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from .db import repo
from .db.models import Interaction
from .db.session import Database
from .engines.registry import EngineRegistry
from .errors import BadRequest
from .jobs import Job, JobManager, Listener
from .pipeline import NoteRequest
from .quotas import QuotaService

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20_000


class NoteService:
    def __init__(self, db: Database, registry: EngineRegistry, jobs: JobManager, quotas: QuotaService):
        self.db = db
        self.registry = registry
        self.jobs = jobs
        self.quotas = quotas

    async def resolve_engine(self, user_id: str, requested: str | None = None) -> str:
        if requested:
            return self.registry.get(requested).name  # raises EngineUnavailable
        user = await self.db.run(repo.get_user, user_id)
        return self.registry.resolve(user.engine_preference if user else None).name

    async def submit(
        self,
        *,
        user_id: str,
        channel: str,
        audio_path: Path | None = None,
        text: str | None = None,
        engine: str | None = None,
        external_id: str | None = None,
        listener: Listener | None = None,
    ) -> tuple[Job | None, Interaction]:
        """Queue a note. Takes ownership of `audio_path` (it is deleted when processing ends
        or if submission fails). Returns (None, existing_note) for a duplicate external_id,
        which makes webhook redeliveries harmless."""
        try:
            if (audio_path is None) == (text is None):
                raise BadRequest(detail="exactly one of audio_path or text is required")
            if text is not None:
                text = text.strip()
                if not text:
                    raise BadRequest("The note is empty.")
                if len(text) > MAX_TEXT_CHARS:
                    raise BadRequest(f"That text is too long. Please keep it under {MAX_TEXT_CHARS:,} characters.")

            if external_id:
                existing = await self.db.run(repo.find_by_external_id, external_id)
                if existing is not None:
                    self._discard(audio_path)
                    return None, existing

            engine_name = await self.resolve_engine(user_id, engine)
            await self.db.run(self.quotas.check, user_id, engine_name, private_available=self.registry.has("private"))
            source = "audio" if audio_path is not None else "text"
            try:
                note = await self.db.run(
                    repo.create_note,
                    user_id=user_id,
                    channel=channel,
                    engine=engine_name,
                    source=source,
                    external_id=external_id,
                )
            except IntegrityError:
                # The same webhook delivery raced us to the insert.
                existing = await self.db.run(repo.find_by_external_id, external_id)
                self._discard(audio_path)
                return None, existing

            request = NoteRequest(
                note_id=note.id,
                user_id=user_id,
                engine=engine_name,
                source=source,
                audio_path=audio_path,
                text=text,
            )
            job = await self.jobs.submit(request, listener)
            return job, note
        except BaseException:
            self._discard(audio_path)
            raise

    @staticmethod
    def _discard(audio_path: Path | None) -> None:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
