"""The one entry point every channel (web, Telegram, WhatsApp) uses to create notes."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from .db import repo
from .db.models import Interaction, utcnow
from .db.session import Database
from .engines.registry import EngineRegistry
from .errors import BadRequest, QuotaExceeded
from .jobs import Job, JobManager, Listener
from .pipeline import NoteRequest
from .quotas import QuotaService

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20_000


class NoteService:
    def __init__(
        self,
        db: Database,
        registry: EngineRegistry,
        jobs: JobManager,
        quotas: QuotaService,
        alert: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.db = db
        self.registry = registry
        self.jobs = jobs
        self.quotas = quotas
        self.alert = alert  # sends a message to the developer (e.g. on Telegram)
        self._alerted_on: date | None = None

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
            try:
                await self.db.run(
                    self.quotas.check, user_id, engine_name, private_available=self.registry.has("private")
                )
            except QuotaExceeded as exc:
                await self.db.run(repo.add_event, user_id, "quota_hit", channel=channel, detail=exc.limit)
                raise
            if engine_name == "cloud":
                await self._maybe_alert_capacity()
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

    async def _maybe_alert_capacity(self) -> None:
        """Tell the developer, at most once a day, when the shared free-tier capacity is filling up."""
        threshold = self.quotas.settings.capacity_alert_percent
        today = utcnow().date()
        if self.alert is None or not threshold or self._alerted_on == today:
            return
        notes_pct, audio_pct = await self.db.run(self.quotas.cloud_usage)
        if max(notes_pct, audio_pct) < threshold:
            return
        self._alerted_on = today
        await self.alert(
            f"⚠️ Vaani is at {notes_pct:.0f}% of today's note cap and {audio_pct:.0f}% of the audio-minute cap "
            "(last 24h). New notes are refused at 100%."
        )

    @staticmethod
    def _discard(audio_path: Path | None) -> None:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
