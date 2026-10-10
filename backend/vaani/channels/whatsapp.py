"""WhatsApp channel over Meta's WhatsApp Cloud API (webhooks in, Graph API out).

Cost control: Meta's pricing page lists replies inside the 24-hour customer-service window
as free, but billing for service messages has been changing, so outgoing messages are
capped per day (WHATSAPP_DAILY_MESSAGE_CAP). Typing indicators are read receipts, not
messages, and are used instead of "processing..." texts to keep the count low.

Phone numbers are personal data: users are stored under a keyed hash (wa_<hash>), and the
raw number is only held in memory for as long as a reply is pending.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..audio import ALLOWED_EXTENSIONS
from ..context import AppContext
from ..db import repo
from ..db.models import Interaction, NoteStatus
from ..errors import VaaniError
from ..security import pseudonymize
from .common import MIN_TEXT_NOTE_WORDS, NoteView, format_note_whatsapp, split_text

log = logging.getLogger(__name__)

BODY_LIMIT = 4096
BUTTON_BODY_LIMIT = 1024
_MIME_EXT = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "audio/webm": ".webm",
}
HELP = (
    "👋 *Hi, I'm Vaani!*\n\n"
    "Send me a voice note in Hindi, English or Hinglish (or forward one) and I'll reply with a summary, "
    "action items and the full transcript.\n\n"
    "Commands: *history* (recent notes), *delete all*, *privacy*, *mode*, *help*."
)


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def _view(note: Interaction) -> NoteView:
    return NoteView(
        id=note.id,
        title=note.title or "Your note",
        summary=note.summary or "",
        action_items=note.action_items_list(),
        sentiment=note.sentiment or "Neutral",
        transcript=note.transcript or "",
        audio_duration_sec=note.audio_duration_sec,
        total_ms=note.total_ms,
    )


class DailyCap:
    def __init__(self, limit: int):
        self.limit = limit
        self.day = None
        self.sent = 0

    def take(self) -> bool:
        today = datetime.now(UTC).date()
        if today != self.day:
            self.day, self.sent = today, 0
        if self.limit and self.sent >= self.limit:
            return False
        self.sent += 1
        return True

    @property
    def remaining(self) -> int | None:
        return None if not self.limit else max(0, self.limit - self.sent)


class WhatsAppChannel:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        s = ctx.settings
        self.graph = f"https://graph.facebook.com/{s.whatsapp_graph_version}"
        self.messages_url = f"{self.graph}/{s.whatsapp_phone_number_id}/messages"
        self.headers = {"Authorization": f"Bearer {s.whatsapp_access_token}"}
        self.cap = DailyCap(s.whatsapp_daily_message_cap)
        self._tasks: set[asyncio.Task] = set()
        self._recent_ids: dict[str, None] = {}  # dedupe webhook redeliveries of non-note messages

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    # ----- inbound ---------------------------------------------------------------------------

    def dispatch(self, payload: dict) -> int:
        """Schedule handling of every message in a webhook payload; returns how many."""
        count = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "messages":
                    continue
                for message in (change.get("value") or {}).get("messages") or []:
                    message_id = message.get("id")
                    if not message_id or message_id in self._recent_ids:
                        continue
                    self._recent_ids[message_id] = None
                    if len(self._recent_ids) > 5000:
                        self._recent_ids.pop(next(iter(self._recent_ids)))
                    task = asyncio.create_task(self._handle(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                    count += 1
        return count

    async def _handle(self, message: dict) -> None:
        wa_id = message.get("from", "")
        user_id = pseudonymize(self.ctx.settings.secret_key, wa_id, "wa_")
        kind = message.get("type")
        try:
            if kind in ("audio", "video", "document"):
                media = message.get(kind) or {}
                mime = (media.get("mime_type") or "").split(";")[0].lower()
                if kind == "document" and not (mime.startswith("audio/") or mime.startswith("video/")):
                    await self.send_text(wa_id, "That file isn't audio. Send me a voice note 🎙️")
                    return
                await self._typing(message["id"])
                await self._process_media(wa_id, user_id, message["id"], media, mime)
            elif kind == "text":
                await self._on_text(wa_id, user_id, message["id"], (message.get("text") or {}).get("body", ""))
            elif kind == "interactive":
                reply = (message.get("interactive") or {}).get("button_reply") or {}
                await self._on_button(wa_id, user_id, reply.get("id", ""))
            else:
                await self.send_text(wa_id, "I work with voice notes and text. Send me a voice note 🎙️")
        except VaaniError as exc:
            await self.send_text(wa_id, f"⚠️ {exc.user_message}")
        except Exception:
            log.exception("WhatsApp message handling failed")
            await self.send_text(wa_id, "⚠️ Vaani is getting a lot of traffic right now. Please try again in a minute.")

    async def _on_text(self, wa_id: str, user_id: str, message_id: str, body: str) -> None:
        text = body.strip()
        command = text.lower().strip("!/. ")
        if command in ("hi", "hello", "hey", "help", "start", "namaste"):
            await self.send_text(wa_id, HELP)
        elif command == "privacy":
            lines = ["🔒 *Where your audio goes*"]
            lines += [f"*{e.label} mode:* {e.description}" for e in self.ctx.registry.available()]
            lines.append("Audio is deleted right after transcription. Reply *delete all* to erase your notes.")
            if self.ctx.settings.public_base_url:
                lines.append(f"Full policy: {self.ctx.settings.public_base_url}/privacy")
            await self.send_text(wa_id, "\n".join(lines))
        elif command == "history":
            notes = await self.ctx.db.run(repo.list_notes, user_id, 5, done_only=True)
            if not notes:
                await self.send_text(wa_id, "📭 No notes yet. Send me a voice note!")
                return
            lines = ["📚 *Your recent notes*"]
            for i, note in enumerate(notes, 1):
                date = note.timestamp.strftime("%d %b") if note.timestamp else ""
                lines.append(f"{i}. {(note.title or note.summary or 'Note')[:70]} ({date})")
            await self.send_text(wa_id, "\n".join(lines))
        elif command == "delete all":
            await self.send_buttons(
                wa_id,
                "Permanently delete all your notes? This can't be undone.",
                [("del:yes", "🗑️ Yes, delete"), ("del:no", "Cancel")],
            )
        elif command == "mode":
            engines = self.ctx.registry.available()
            if len(engines) < 2:
                await self.send_text(wa_id, f"This server runs {engines[0].label if engines else 'Fast'} mode only.")
            else:
                await self.send_buttons(
                    wa_id, "How should I process your notes?", [(f"mode:{e.name}", e.label) for e in engines]
                )
        elif len(text.split()) >= MIN_TEXT_NOTE_WORDS:
            await self._typing(message_id)
            await self._run_note(wa_id, user_id, text=text, external_id=f"wa:{message_id}")
        else:
            await self.send_text(wa_id, HELP)

    async def _on_button(self, wa_id: str, user_id: str, button_id: str) -> None:
        action, _, rest = button_id.partition(":")
        if action == "tr":
            note = await self.ctx.db.run(repo.get_user_note, user_id, rest)
            if note is None or not note.transcript:
                await self.send_text(wa_id, "That transcript is no longer available.")
                return
            for chunk in split_text("📄 *Full transcript*\n\n" + note.transcript, BODY_LIMIT):
                await self.send_text(wa_id, chunk)
        elif action == "rate":
            rating, _, note_id = rest.partition(":")
            value = "thumbs_up" if rating == "up" else "thumbs_down"
            if await self.ctx.db.run(repo.rate_note, user_id, note_id, value):
                await self.send_text(wa_id, "🙏 Thanks for the feedback!")
        elif action == "mode" and self.ctx.registry.has(rest):
            await self.ctx.db.run(repo.set_engine_preference, user_id, rest)
            engine = self.ctx.registry.get(rest)
            await self.send_text(wa_id, f"✅ {engine.label} mode is on. {engine.description}")
        elif action == "del":
            if rest == "yes":
                count = await self.ctx.db.run(repo.delete_notes, user_id, None)
                await self.send_text(wa_id, f"✅ Deleted {count} note(s) permanently.")
            else:
                await self.send_text(wa_id, "👍 Cancelled. Your notes are untouched.")

    async def _process_media(self, wa_id: str, user_id: str, message_id: str, media: dict, mime: str) -> None:
        media_id = media.get("id")
        ext = Path(media.get("filename") or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            ext = _MIME_EXT.get(mime, ".ogg")
        meta = (await self._graph_get(f"{self.graph}/{media_id}")).json()
        if meta.get("file_size") and int(meta["file_size"]) > self.ctx.settings.max_upload_bytes:
            await self.send_text(
                wa_id, f"📁 That file is over {self.ctx.settings.max_upload_mb} MB. Please send a shorter one."
            )
            return
        path = self.ctx.settings.temp_dir / f"{uuid.uuid4().hex}{ext}"
        try:
            async with self.ctx.http.stream("GET", meta["url"], headers=self.headers, timeout=120) as response:
                response.raise_for_status()
                with path.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        fh.write(chunk)
        except (httpx.HTTPError, KeyError) as exc:
            path.unlink(missing_ok=True)
            log.warning("WhatsApp media download failed: %s", exc)
            await self.send_text(wa_id, "I couldn't download that audio. Please try sending it again.")
            return
        await self._run_note(wa_id, user_id, audio_path=path, external_id=f"wa:{message_id}")

    async def _run_note(
        self, wa_id: str, user_id: str, *, external_id: str, audio_path: Path | None = None, text: str | None = None
    ) -> None:
        job, note = await self.ctx.notes.submit(
            user_id=user_id, channel="whatsapp", audio_path=audio_path, text=text, external_id=external_id
        )
        if job is None:
            return  # duplicate delivery of a message we already handled
        await self.ctx.jobs.wait_until_done(job)
        if job.status != NoteStatus.DONE:
            await self.send_text(
                wa_id, f"⚠️ {job.error or 'Vaani is getting a lot of traffic right now. Please try again in a minute.'}"
            )
            return
        note = await self.ctx.db.run(repo.get_note, job.id)
        await self.send_result(wa_id, note)

    async def send_result(self, wa_id: str, note: Interaction) -> None:
        body = format_note_whatsapp(_view(note))
        buttons = [
            (f"tr:{note.id}", "📄 Transcript"),
            (f"rate:up:{note.id}", "👍 Useful"),
            (f"rate:down:{note.id}", "👎 Not useful"),
        ]
        if len(body) <= BUTTON_BODY_LIMIT:
            await self.send_buttons(wa_id, body, buttons)
            return
        for chunk in split_text(body, BODY_LIMIT):
            await self.send_text(wa_id, chunk)
        await self.send_buttons(wa_id, "Want the full transcript, or to rate this summary?", buttons)

    # ----- outbound --------------------------------------------------------------------------

    async def _graph_get(self, url: str) -> httpx.Response:
        response = await self.ctx.http.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return response

    async def _post_message(self, payload: dict) -> bool:
        if not self.cap.take():
            log.warning("WhatsApp daily message cap (%d) reached; not sending", self.cap.limit)
            return False
        try:
            response = await self.ctx.http.post(self.messages_url, headers=self.headers, json=payload, timeout=30)
        except httpx.HTTPError as exc:
            log.warning("WhatsApp send failed: %s", exc)
            return False
        if not response.is_success:
            log.warning("WhatsApp send failed: HTTP %d %s", response.status_code, response.text[:300])
            return False
        return True

    async def send_text(self, wa_id: str, text: str) -> bool:
        if self.cap.remaining == 1:
            text = "Vaani's WhatsApp line has reached today's free limit. Please try again tomorrow" + (
                f", or use the web app: {self.ctx.settings.public_base_url}"
                if self.ctx.settings.public_base_url
                else "."
            )
        return await self._post_message(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_id,
                "type": "text",
                "text": {"body": text[:BODY_LIMIT], "preview_url": False},
            }
        )

    async def send_buttons(self, wa_id: str, body: str, buttons: list[tuple[str, str]]) -> bool:
        if self.cap.remaining == 1:
            return await self.send_text(wa_id, "")
        return await self._post_message(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:BUTTON_BODY_LIMIT]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}}
                            for bid, title in buttons[:3]
                        ]
                    },
                },
            }
        )

    async def _typing(self, message_id: str) -> None:
        """Mark as read and show 'typing…' (up to 25 s). A status update, not a message."""
        with contextlib.suppress(httpx.HTTPError):
            await self.ctx.http.post(
                self.messages_url,
                headers=self.headers,
                timeout=15,
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
