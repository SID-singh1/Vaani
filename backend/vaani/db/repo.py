"""Data access. Plain functions taking a Session, so they can run in a worker thread via
Database.run() and be unit-tested without the web layer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Feedback, Interaction, NoteStatus, User, utcnow

INTERRUPTED_MESSAGE = "Processing was interrupted by a server restart. Please send the note again."


def get_or_create_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        session.add(user)
        try:
            session.flush()
        except IntegrityError:  # created concurrently by another request
            session.rollback()
            user = session.get(User, user_id)
    return user


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def claim_legacy_user(session: Session, user_id: str) -> bool:
    """Bind a pre-v2 web id to a signed session, at most once."""
    user = session.get(User, user_id)
    if user is None or user.claimed_at is not None:
        return False
    user.claimed_at = utcnow()
    return True


def set_engine_preference(session: Session, user_id: str, engine: str | None) -> None:
    user = get_or_create_user(session, user_id)
    user.engine_preference = engine


def create_note(
    session: Session,
    *,
    user_id: str,
    channel: str,
    engine: str,
    source: str,
    external_id: str | None = None,
) -> Interaction:
    get_or_create_user(session, user_id)
    note = Interaction(
        user_id=user_id,
        channel=channel,
        engine=engine,
        source=source,
        external_id=external_id,
        status=NoteStatus.QUEUED,
        stage="queued",
    )
    session.add(note)
    session.flush()
    return note


def find_by_external_id(session: Session, external_id: str) -> Interaction | None:
    return session.scalar(select(Interaction).where(Interaction.external_id == external_id))


def set_stage(session: Session, note_id: str, stage: str) -> None:
    session.execute(
        update(Interaction).where(Interaction.id == note_id).values(stage=stage, status=NoteStatus.PROCESSING)
    )


def complete_note(session: Session, note_id: str, result: dict) -> None:
    session.execute(
        update(Interaction)
        .where(Interaction.id == note_id)
        .values(
            status=NoteStatus.DONE,
            stage="done",
            transcript=result["transcript"],
            title=result["title"],
            summary=result["summary"],
            action_items=json.dumps(result["action_items"], ensure_ascii=False),
            sentiment=result["sentiment"],
            audio_duration_sec=result.get("audio_duration_sec"),
            models=result.get("models"),
            asr_ms=result.get("asr_ms"),
            llm_ms=result.get("llm_ms"),
            total_ms=result.get("total_ms"),
            completed_at=utcnow(),
            error_message=None,
        )
    )


def fail_note(session: Session, note_id: str, message: str, *, audio_duration_sec: float | None = None) -> None:
    values: dict = {
        "status": NoteStatus.FAILED,
        "stage": "failed",
        "error_message": message,
        "completed_at": utcnow(),
    }
    if audio_duration_sec is not None:
        values["audio_duration_sec"] = audio_duration_sec
    session.execute(update(Interaction).where(Interaction.id == note_id).values(**values))


def mark_interrupted(session: Session) -> int:
    result = session.execute(
        update(Interaction)
        .where(Interaction.status.in_(NoteStatus.ACTIVE))
        .values(status=NoteStatus.FAILED, stage="failed", error_message=INTERRUPTED_MESSAGE, completed_at=utcnow())
    )
    return result.rowcount or 0


def get_note(session: Session, note_id: str) -> Interaction | None:
    return session.get(Interaction, note_id)


def get_user_note(session: Session, user_id: str, note_id: str) -> Interaction | None:
    note = session.get(Interaction, note_id)
    if note is None or note.user_id != user_id:
        return None
    return note


def list_notes(session: Session, user_id: str, limit: int = 20, *, done_only: bool = False) -> list[Interaction]:
    query = select(Interaction).where(Interaction.user_id == user_id)
    if done_only:
        query = query.where(
            (Interaction.status == NoteStatus.DONE)
            | ((Interaction.status.is_(None)) & (Interaction.error_message.is_(None)))
        )
    query = query.order_by(Interaction.timestamp.desc()).limit(limit)
    return list(session.scalars(query))


def delete_notes(session: Session, user_id: str, note_ids: list[str] | None = None) -> int:
    query = session.query(Interaction).filter(Interaction.user_id == user_id)
    if note_ids is not None:
        if not note_ids:
            return 0
        query = query.filter(Interaction.id.in_(note_ids))
    return query.delete(synchronize_session=False)


def rate_note(session: Session, user_id: str, note_id: str, rating: str) -> bool:
    note = get_user_note(session, user_id, note_id)
    if note is None:
        return False
    note.accuracy_rating = rating
    return True


def add_feedback(session: Session, user_id: str, message: str) -> None:
    get_or_create_user(session, user_id)
    session.add(Feedback(user_id=user_id, message=message[:4000]))


def count_user_notes_since(session: Session, user_id: str, since: datetime) -> int:
    return (
        session.scalar(
            select(func.count(Interaction.id)).where(
                Interaction.user_id == user_id,
                Interaction.timestamp >= since,
                _not_failed(),
            )
        )
        or 0
    )


def oldest_user_note_since(session: Session, user_id: str, since: datetime) -> datetime | None:
    return session.scalar(
        select(func.min(Interaction.timestamp)).where(
            Interaction.user_id == user_id,
            Interaction.timestamp >= since,
            _not_failed(),
        )
    )


def count_engine_notes_since(session: Session, engine: str, since: datetime) -> int:
    return (
        session.scalar(
            select(func.count(Interaction.id)).where(
                Interaction.engine == engine,
                Interaction.timestamp >= since,
                _not_failed(),
            )
        )
        or 0
    )


def _not_failed():
    # Legacy rows have NULL status; treat them as done rather than letting NULL compare false.
    return func.coalesce(Interaction.status, NoteStatus.DONE) != NoteStatus.FAILED


def day_ago() -> datetime:
    return utcnow() - timedelta(days=1)
