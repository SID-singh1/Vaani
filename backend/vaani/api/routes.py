"""Public JSON API (v1) used by the web client."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field

from ..audio import ALLOWED_EXTENSIONS
from ..context import AppContext
from ..db import repo
from ..db.models import Interaction, NoteStatus
from ..errors import BadRequest, FileTooLarge, NotFound, UnsupportedFormat
from ..security import LEGACY_WEB_ID, issue_token, new_web_user_id
from .deps import current_user, get_ctx
from .ratelimit import client_ip

router = APIRouter(prefix="/api/v1")

_MIME_EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "audio/flac": ".flac",
    "video/quicktime": ".mov",
    "audio/amr": ".amr",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def note_payload(note: Interaction, progress: dict | None = None) -> dict:
    status = note.effective_status
    payload = {
        "id": note.id,
        "status": status,
        "stage": (progress or {}).get("stage") or note.stage or status,
        "created_at": _iso(note.timestamp),
        "completed_at": _iso(note.completed_at),
        "channel": note.channel,
        "engine": note.engine,
        "source": note.source,
        "title": note.title,
        "summary": note.summary,
        "action_items": note.action_items_list(),
        "sentiment": note.sentiment if status == NoteStatus.DONE else None,
        "transcript": note.transcript,
        "audio_duration_sec": note.audio_duration_sec,
        "rating": {"thumbs_up": "up", "thumbs_down": "down"}.get(note.accuracy_rating or ""),
        "error": note.error_message if status == NoteStatus.FAILED else None,
        "models": note.models,
        "timings": {"asr_ms": note.asr_ms, "llm_ms": note.llm_ms, "total_ms": note.total_ms},
        "queue_position": None,
        "eta_seconds": None,
    }
    if progress:
        payload["queue_position"] = progress.get("queue_position")
        payload["eta_seconds"] = progress.get("eta_seconds")
    return payload


def _engine_payload(engine) -> dict:
    return {
        "name": engine.name,
        "label": engine.label,
        "description": engine.description,
        "tags": engine.tags,
        "max_audio_minutes": engine.max_audio_seconds // 60,
    }


class SessionRequest(BaseModel):
    legacy_user_id: str | None = Field(default=None, max_length=40)


@router.post("/session")
async def create_session(request: Request, body: SessionRequest | None = None, ctx: AppContext = Depends(get_ctx)):
    request.app.state.session_limiter.hit(client_ip(request, ctx.settings.trusted_proxy_hops))
    migrated = False
    user_id = new_web_user_id()
    legacy = body.legacy_user_id if body else None
    if legacy and LEGACY_WEB_ID.match(legacy) and await ctx.db.run(repo.claim_legacy_user, legacy):
        user_id, migrated = legacy, True
    return {"token": issue_token(ctx.settings.secret_key, user_id), "user_id": user_id, "migrated": migrated}


@router.get("/engines")
async def list_engines(ctx: AppContext = Depends(get_ctx)):
    return {"default": ctx.registry.default, "engines": [_engine_payload(e) for e in ctx.registry.available()]}


@router.get("/me")
async def get_me(user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)):
    user = await ctx.db.run(repo.get_user, user_id)
    preference = user.engine_preference if user else None
    return {
        "user_id": user_id,
        "engine": ctx.registry.resolve(preference).name if ctx.registry.available() else None,
        "engine_preference": preference,
        "usage": await ctx.db.run(ctx.quotas.usage, user_id),
        "engines": [_engine_payload(e) for e in ctx.registry.available()],
    }


class PreferencesRequest(BaseModel):
    engine: Literal["cloud", "private"]


@router.patch("/me")
async def update_me(body: PreferencesRequest, user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)):
    ctx.registry.get(body.engine)  # 503 if this server doesn't offer it
    await ctx.db.run(repo.set_engine_preference, user_id, body.engine)
    return await get_me(user_id, ctx)


async def _save_upload(upload: UploadFile, ctx: AppContext) -> Path:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        ext = _MIME_EXTENSIONS.get(content_type, ext)
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedFormat()
    dest = ctx.settings.temp_dir / f"{uuid.uuid4().hex}{ext}"
    limit = ctx.settings.max_upload_bytes
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise FileTooLarge(f"That file is too large. The limit is {ctx.settings.max_upload_mb} MB.")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise BadRequest("The uploaded file is empty.")
    return dest


@router.post("/notes", status_code=202)
async def create_note(
    request: Request,
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
    engine: str | None = Form(None),
    user_id: str = Depends(current_user),
    ctx: AppContext = Depends(get_ctx),
):
    if audio is None and not (text and text.strip()):
        raise BadRequest("Send an audio file or some text.")
    path = await _save_upload(audio, ctx) if audio is not None else None
    _, note = await ctx.notes.submit(
        user_id=user_id, channel="web", audio_path=path, text=None if path else text, engine=engine or None
    )
    return note_payload(note, ctx.jobs.progress(note.id))


@router.get("/notes")
async def list_notes(
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(current_user),
    ctx: AppContext = Depends(get_ctx),
):
    notes = await ctx.db.run(repo.list_notes, user_id, limit)
    return {"notes": [note_payload(n, ctx.jobs.progress(n.id)) for n in notes]}


@router.get("/notes/{note_id}")
async def get_note(
    note_id: str,
    wait: int = Query(0, ge=0, le=25, description="Long-poll: wait up to N seconds for progress"),
    user_id: str = Depends(current_user),
    ctx: AppContext = Depends(get_ctx),
):
    note = await ctx.db.run(repo.get_user_note, user_id, note_id)
    if note is None:
        raise NotFound("That note doesn't exist or was deleted.")
    if wait and note.effective_status in NoteStatus.ACTIVE:
        await ctx.jobs.wait_for_change(note_id, wait)
        note = await ctx.db.run(repo.get_user_note, user_id, note_id) or note
    return note_payload(note, ctx.jobs.progress(note_id))


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)):
    deleted = await ctx.db.run(repo.delete_notes, user_id, [note_id])
    if not deleted:
        raise NotFound("That note doesn't exist or was already deleted.")
    return {"deleted": deleted}


@router.delete("/notes")
async def delete_all_notes(user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)):
    return {"deleted": await ctx.db.run(repo.delete_notes, user_id, None)}


class RatingRequest(BaseModel):
    rating: Literal["up", "down"]


@router.post("/notes/{note_id}/rating")
async def rate_note(
    note_id: str, body: RatingRequest, user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)
):
    value = "thumbs_up" if body.rating == "up" else "thumbs_down"
    if not await ctx.db.run(repo.rate_note, user_id, note_id, value):
        raise NotFound("That note doesn't exist.")
    return {"ok": True}


class FeedbackRequest(BaseModel):
    message: str = Field(min_length=2, max_length=4000)


@router.post("/feedback")
async def send_feedback(
    body: FeedbackRequest, user_id: str = Depends(current_user), ctx: AppContext = Depends(get_ctx)
):
    await ctx.db.run(repo.add_feedback, user_id, body.message.strip())
    return {"ok": True}
