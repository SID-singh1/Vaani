"""Telegram bot, running inside the API process.

Polling for local development; webhooks in production. Webhooks matter on free hosting:
an instance that sleeps after inactivity can't poll, but Telegram's webhook request
wakes it up, so no keep-alive traffic is needed.
"""

from __future__ import annotations

import contextlib
import html
import logging
import re
import uuid
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest

from ..audio import ALLOWED_EXTENSIONS
from ..context import AppContext
from ..db import repo
from ..db.models import Interaction, NoteStatus
from ..errors import QuotaExceeded, VaaniError
from ..jobs import Job
from ..security import derive_secret
from .common import MIN_TEXT_NOTE_WORDS, PRO_FEATURES, STAGE_TEXT, NoteView, format_note_html, queued_text, split_text

log = logging.getLogger(__name__)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API getFile limit
MESSAGE_LIMIT = 4000
HISTORY_PAGE_SIZE = 5
DELETE_LIST_SIZE = 15

_MIME_EXT = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/amr": ".amr",
}

WELCOME = (
    "👋 <b>Welcome to Vaani!</b>\n\n"
    "Send me a voice note in <b>Hindi, English or Hinglish</b> and I'll reply with a clean summary, "
    "action items (who does what, by when) and the full transcript in Romanized Hinglish.\n\n"
    "You can also forward voice notes from WhatsApp, send audio/video files, or paste long text.\n\n"
    "<b>Commands</b>\n"
    "/history – your recent notes\n"
    "/delete – delete notes\n"
    "/mode – choose how notes are processed\n"
    "/privacy – where your audio goes\n"
    "/pro – what's coming in Vaani Pro\n"
    "/feedback &lt;text&gt; – tell the developer what to improve"
)

PRO_TEXT = (
    "⭐ <b>Vaani Pro</b> (coming soon)\n\n"
    + "\n".join(f"• {feature}" for feature in PRO_FEATURES)
    + "\n\nJoin the waitlist and you'll be the first to know when it launches."
)


def _view(note: Interaction) -> NoteView:
    return NoteView(
        id=note.id,
        title=note.title or "Your note",
        summary=note.summary or "",
        action_items=note.action_items_list(),
        sentiment=note.sentiment or "Neutral",
        transcript=note.transcript or "",
        engine=note.engine,
        audio_duration_sec=note.audio_duration_sec,
        total_ms=note.total_ms,
    )


def _result_keyboard(note_id: str, rated: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📄 Full transcript", callback_data=f"tr:{note_id}")]]
    if not rated:
        # "Useful?" rather than "Accurate?": users can judge usefulness at a glance,
        # while accuracy would mean re-listening to their own audio.
        rows.append(
            [
                InlineKeyboardButton("👍 Useful", callback_data=f"rate:up:{note_id}"),
                InlineKeyboardButton("👎 Not useful", callback_data=f"rate:down:{note_id}"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def _without_buttons(markup: InlineKeyboardMarkup | None, prefix: str) -> InlineKeyboardMarkup | None:
    """The same keyboard minus buttons whose callback data starts with `prefix` (None if empty)."""
    if markup is None:
        return None
    rows = [
        [button for button in row if not (button.callback_data or "").startswith(prefix)]
        for row in markup.inline_keyboard
    ]
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(rows) if rows else None


def _preview(text: str, limit: int = 110) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _media_extension(file_name: str | None, mime: str | None, default: str) -> str:
    ext = Path(file_name or "").suffix.lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext
    return _MIME_EXT.get((mime or "").split(";")[0].lower(), default)


class TelegramChannel:
    def __init__(self, ctx: AppContext, request: BaseRequest | None = None):
        self.ctx = ctx
        settings = ctx.settings
        self.mode = settings.telegram_mode
        if self.mode == "webhook" and not settings.public_base_url:
            log.warning("TELEGRAM_MODE=webhook but PUBLIC_BASE_URL is unset; falling back to polling.")
            self.mode = "polling"
        self.webhook_secret = settings.telegram_webhook_secret or derive_secret(settings.secret_key, "telegram-webhook")

        builder = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True)
        if self.mode == "webhook":
            builder = builder.updater(None)
        if request is not None:  # tests inject an offline fake of the Bot API
            builder = builder.request(request).get_updates_request(request)
        self.app: Application = builder.build()
        self.app.bot_data["vaani"] = self
        self._register_handlers()

    # ----- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        allowed = ["message", "callback_query"]
        if self.mode == "webhook":
            url = f"{self.ctx.settings.public_base_url}/telegram/webhook"
            await self.app.bot.set_webhook(url=url, secret_token=self.webhook_secret, allowed_updates=allowed)
            log.info("Telegram webhook registered at %s", url)
        else:
            await self.app.updater.start_polling(allowed_updates=allowed)
            log.info("Telegram polling started")
        try:
            await self.app.bot.set_my_commands(
                [
                    BotCommand("history", "Your recent notes"),
                    BotCommand("delete", "Delete notes"),
                    BotCommand("mode", "Choose how notes are processed"),
                    BotCommand("privacy", "Where your audio goes"),
                    BotCommand("pro", "Vaani Pro waitlist"),
                    BotCommand("feedback", "Send feedback"),
                    BotCommand("help", "How to use Vaani"),
                ]
            )
        except TelegramError as exc:
            log.warning("Could not set bot commands: %s", exc)

    async def stop(self) -> None:
        if self.app.updater and self.app.updater.running:
            await self.app.updater.stop()
        if self.app.running:
            await self.app.stop()
        await self.app.shutdown()

    async def feed_webhook(self, payload: dict) -> None:
        await self.app.update_queue.put(Update.de_json(payload, self.app.bot))

    def _register_handlers(self) -> None:
        add = self.app.add_handler
        add(CommandHandler(["start", "help"], self.cmd_start))
        add(CommandHandler("history", self.cmd_history))
        add(CommandHandler("delete", self.cmd_delete))
        add(CommandHandler("mode", self.cmd_mode))
        add(CommandHandler("privacy", self.cmd_privacy))
        add(CommandHandler("feedback", self.cmd_feedback))
        add(CommandHandler("pro", self.cmd_pro))
        media = filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO | filters.Document.ALL
        add(MessageHandler(media, self.on_media))
        add(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        add(MessageHandler(filters.PHOTO | filters.Sticker.ALL, self.on_unsupported))
        add(CallbackQueryHandler(self.on_callback))
        self.app.add_error_handler(self.on_error)

    @staticmethod
    def _user_id(update: Update) -> str:
        return f"tg_{update.effective_user.id}"

    # ----- commands --------------------------------------------------------------------------

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if context.args and context.args[0] == "pro":  # t.me/<bot>?start=pro, from the landing page
            await self._send_pro(update.message, self._user_id(update))
            return
        markup = self._mode_keyboard() if len(self.ctx.registry.available()) > 1 else None
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML, reply_markup=markup)

    async def cmd_privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        engines = self.ctx.registry.available()
        lines = ["🔒 <b>Where your audio goes</b>\n"]
        for engine in engines:
            lines.append(f"<b>{html.escape(engine.label)} mode:</b> {html.escape(engine.description)}")
        lines.append(
            "\nAudio files are deleted as soon as they are transcribed. Transcripts and summaries are kept so "
            "/history works; /delete removes them permanently."
        )
        if self.ctx.settings.public_base_url:
            lines.append(f"\nFull policy: {self.ctx.settings.public_base_url}/privacy")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    def _mode_keyboard(self) -> InlineKeyboardMarkup:
        icons = {"cloud": "⚡", "private": "🔒"}
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(f"{icons.get(e.name, '')} {e.label}", callback_data=f"mode:{e.name}")
                    for e in self.ctx.registry.available()
                ]
            ]
        )

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        engines = self.ctx.registry.available()
        if len(engines) < 2:
            label = engines[0].label if engines else "Fast"
            await update.message.reply_text(
                f"This server runs <b>{html.escape(label)}</b> mode only. See /privacy for details.",
                parse_mode=ParseMode.HTML,
            )
            return
        current = await self.ctx.notes.resolve_engine(self._user_id(update))
        lines = [f"Current mode: <b>{html.escape(self.ctx.registry.get(current).label)}</b>\n"]
        lines += [f"<b>{html.escape(e.label)}</b>: {html.escape(e.description)}" for e in engines]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=self._mode_keyboard())

    async def cmd_pro(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_pro(update.message, self._user_id(update))

    async def _send_pro(self, message: Message, user_id: str) -> None:
        if await self.ctx.db.run(repo.on_waitlist, user_id):
            await message.reply_text(PRO_TEXT + "\n\n✅ You're already on the waitlist.", parse_mode=ParseMode.HTML)
            return
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⭐ Join the waitlist", callback_data="pro:join")]])
        await message.reply_text(PRO_TEXT, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = " ".join(context.args or []).strip()
        if len(text) < 2:
            await update.message.reply_text(
                "Write your feedback after the command, e.g.\n/feedback please add PDF export"
            )
            return
        await self.ctx.db.run(repo.add_feedback, self._user_id(update), text)
        await update.message.reply_text("🙏 Thank you! Your feedback goes straight to the developer.")

    async def _history_page(self, user_id: str, page: int) -> tuple[str, InlineKeyboardMarkup] | None:
        """One page of history: HISTORY_PAGE_SIZE notes, numbered buttons to open them, and Older/Newer arrows."""
        offset = page * HISTORY_PAGE_SIZE
        # One extra row tells us whether an older page exists without a separate count query.
        notes = await self.ctx.db.run(repo.list_notes, user_id, HISTORY_PAGE_SIZE + 1, done_only=True, offset=offset)
        has_older, notes = len(notes) > HISTORY_PAGE_SIZE, notes[:HISTORY_PAGE_SIZE]
        if not notes:
            return None
        first = offset + 1
        heading = "Your recent notes" if page == 0 else f"Notes {first}–{offset + len(notes)}"
        lines = [f"📚 <b>{heading}</b>"]
        for i, note in enumerate(notes, first):
            date = note.timestamp.strftime("%d %b") if note.timestamp else ""
            title = html.escape(note.title or "Note")
            actions = len(note.action_items_list())
            meta = f"{date} · {actions} action item{'s' if actions != 1 else ''}"
            lines.append(f"\n<b>{i}. {title}</b>\n<i>{meta}</i>\n{html.escape(_preview(note.summary or ''))}")
        lines.append("\nTap a number to open the full note.")
        rows = [[InlineKeyboardButton(str(i), callback_data=f"open:{note.id}") for i, note in enumerate(notes, first)]]
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Newer", callback_data=f"hist:{page - 1}"))
        if has_older:
            nav.append(InlineKeyboardButton("Older ▶", callback_data=f"hist:{page + 1}"))
        if nav:
            rows.append(nav)
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self._history_page(self._user_id(update), 0)
        if result is None:
            await update.message.reply_text("📭 No notes yet. Send me a voice note to get started!")
            return
        text, keyboard = result
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        notes = await self.ctx.db.run(repo.list_notes, self._user_id(update), DELETE_LIST_SIZE)
        if not notes:
            await update.message.reply_text("📭 You don't have any saved notes.")
            return
        context.user_data["delete_map"] = {str(i): n.id for i, n in enumerate(notes, 1)}
        lines = ["🗑️ <b>Which notes should I delete?</b>\n"]
        for i, note in enumerate(notes, 1):
            date = note.timestamp.strftime("%d %b") if note.timestamp else ""
            title = html.escape((note.title or note.summary or note.error_message or "Note")[:70])
            lines.append(f"<b>{i}.</b> {title} <i>({date})</i>")
        lines.append(
            "\nReply with numbers (e.g. <code>1, 3</code>) or <code>all</code>. Reply <code>cancel</code> to stop."
        )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ----- messages --------------------------------------------------------------------------

    async def on_unsupported(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("I work with voice notes, audio/video files and text. Send me one of those! 🎙️")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "").strip()
        if context.user_data.get("delete_map") and await self._handle_delete_reply(update, context, text):
            return
        if len(text.split()) < MIN_TEXT_NOTE_WORDS:
            await update.message.reply_text(
                "Send me a voice note 🎙️ (or paste a longer message) and I'll summarize it with action items. "
                "Type /help to see everything I can do."
            )
            return
        await self._process(update, context, text=text)

    async def _handle_delete_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
        delete_map: dict = context.user_data["delete_map"]
        lowered = text.lower()
        if lowered in ("cancel", "stop", "exit"):
            context.user_data.pop("delete_map", None)
            await update.message.reply_text("👍 Cancelled. Your notes are untouched.")
            return True
        if lowered == "all":
            ids, label = None, "ALL your notes"
        elif re.fullmatch(r"[\d,\s]+", text):
            picks = list(dict.fromkeys(n for n in re.findall(r"\d+", text) if n in delete_map))
            if not picks:
                await update.message.reply_text("Those numbers aren't in the list. Try again, or reply cancel.")
                return True
            ids, label = [delete_map[n] for n in picks], "note(s) " + ", ".join(f"#{n}" for n in picks)
        else:
            context.user_data.pop("delete_map", None)  # they moved on; treat as a normal message
            return False
        context.user_data["delete_pending"] = ids
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑️ Yes, delete", callback_data="del:yes"),
                    InlineKeyboardButton("Cancel", callback_data="del:no"),
                ]
            ]
        )
        await update.message.reply_text(
            f"Permanently delete {html.escape(label)}? This can't be undone.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        return True

    async def on_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message.voice:
            media, ext = message.voice, ".ogg"
        elif message.audio:
            media = message.audio
            ext = _media_extension(media.file_name, media.mime_type, ".mp3")
        elif message.video_note:
            media, ext = message.video_note, ".mp4"
        elif message.video:
            media = message.video
            ext = _media_extension(media.file_name, media.mime_type, ".mp4")
        else:
            media = message.document
            mime = (media.mime_type or "").lower()
            if not (mime.startswith("audio/") or mime.startswith("video/")):
                await message.reply_text("That file isn't audio or video. Send a voice note or an audio file 🎙️")
                return
            ext = _media_extension(media.file_name, media.mime_type, ".bin")
        if ext not in ALLOWED_EXTENSIONS:
            await message.reply_text("That audio format isn't supported. Try mp3, m4a, ogg, wav or mp4.")
            return
        limit = min(TELEGRAM_DOWNLOAD_LIMIT, self.ctx.settings.max_upload_bytes)
        if media.file_size and media.file_size > limit:
            await message.reply_text(
                f"📁 That file is over {limit // (1024 * 1024)} MB, the most a Telegram bot can download. "
                "Please send a shorter recording."
            )
            return
        path = self.ctx.settings.temp_dir / f"{uuid.uuid4().hex}{ext}"
        try:
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=path)
        except TelegramError as exc:
            path.unlink(missing_ok=True)
            log.warning("Telegram download failed: %s", exc)
            await message.reply_text("I couldn't download that file from Telegram. Please try sending it again.")
            return
        await self._process(update, context, audio_path=path)

    async def _process(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        audio_path: Path | None = None,
        text: str | None = None,
    ) -> None:
        message = update.message
        status: Message | None = None

        async def on_progress(job: Job, stage: str) -> None:
            label = STAGE_TEXT.get(stage)
            if status is None or label is None or stage == "queued":
                return
            with contextlib.suppress(TelegramError):  # e.g. already replaced by the result
                await status.edit_text(label)

        try:
            job, note = await self.ctx.notes.submit(
                user_id=self._user_id(update),
                channel="telegram",
                audio_path=audio_path,
                text=text,
                external_id=f"tg:{message.chat_id}:{message.message_id}",
                listener=on_progress,
            )
        except VaaniError as exc:
            hint = "\n\nWant more? See /pro." if isinstance(exc, QuotaExceeded) and exc.personal else ""
            await message.reply_text(f"⚠️ {exc.user_message}{hint}")
            return

        if job is None:  # Telegram redelivered an update we've already handled
            if note.effective_status == NoteStatus.DONE:
                await self._send_result(message, note)
            return

        progress = self.ctx.jobs.progress(job.id) or {}
        status = await message.reply_text(queued_text(progress.get("queue_position"), progress.get("eta_seconds")))
        await self.ctx.jobs.wait_until_done(job)

        if job.status == NoteStatus.DONE:
            note = await self.ctx.db.run(repo.get_note, job.id)
            await self._send_result(message, note)
            await self._safe_delete(status)
        else:
            text_out = f"⚠️ {job.error or 'Vaani is getting a lot of traffic right now. Please try again in a minute.'}"
            try:
                await status.edit_text(text_out)
            except TelegramError:
                await message.reply_text(text_out)

    async def _send_result(self, message: Message, note: Interaction) -> None:
        chunks = split_text(format_note_html(_view(note)), MESSAGE_LIMIT)
        for i, chunk in enumerate(chunks):
            markup = _result_keyboard(note.id, rated=bool(note.accuracy_rating)) if i == len(chunks) - 1 else None
            await message.reply_text(chunk, parse_mode=ParseMode.HTML, reply_markup=markup)

    @staticmethod
    async def _safe_delete(message: Message | None) -> None:
        if message is None:
            return
        with contextlib.suppress(TelegramError):
            await message.delete()

    # ----- buttons ---------------------------------------------------------------------------

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        data = query.data or ""
        user_id = self._user_id(update)
        action, _, rest = data.partition(":")

        if action == "tr":
            note = await self.ctx.db.run(repo.get_user_note, user_id, rest)
            if note is None or not note.transcript:
                await query.answer("That transcript is no longer available.", show_alert=True)
                return
            await query.answer()
            await self.ctx.db.run(repo.add_event, user_id, "transcript_open", note_id=note.id, channel="telegram")
            with contextlib.suppress(TelegramError):  # one tap, one transcript
                await query.edit_message_reply_markup(_without_buttons(query.message.reply_markup, "tr:"))
            body = "📄 <b>Full transcript</b>\n\n" + html.escape(note.transcript)
            for chunk in split_text(body, MESSAGE_LIMIT):
                await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        elif action == "open":
            note = await self.ctx.db.run(repo.get_user_note, user_id, rest)
            if note is None or note.effective_status != NoteStatus.DONE:
                await query.answer("That note is no longer available.", show_alert=True)
                return
            await query.answer()
            await self.ctx.db.run(repo.add_event, user_id, "history_open", note_id=note.id, channel="telegram")
            await self._send_result(query.message, note)

        elif action == "hist":
            page = max(0, int(rest)) if rest.isdigit() else 0
            result = await self._history_page(user_id, page)
            await query.answer()
            if result is not None:
                text, keyboard = result
                with contextlib.suppress(TelegramError):  # same page tapped twice: "message is not modified"
                    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif action == "pro":
            joined = await self.ctx.db.run(repo.join_waitlist, user_id, channel="telegram", source="command")
            await query.answer("You're on the list! 🎉" if joined else "You're already on the list.")
            with contextlib.suppress(TelegramError):
                await query.edit_message_text(
                    PRO_TEXT + "\n\n✅ You're on the waitlist. Thanks!", parse_mode=ParseMode.HTML
                )

        elif action == "rate":
            rating, _, note_id = rest.partition(":")
            value = "thumbs_up" if rating == "up" else "thumbs_down"
            if not await self.ctx.db.run(repo.rate_note, user_id, note_id, value):
                await query.answer("That note no longer exists.")
                return
            await query.answer("Thanks for the feedback! 🙏")
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(_without_buttons(query.message.reply_markup, "rate:"))

        elif action == "mode":
            if not self.ctx.registry.has(rest):
                await query.answer("That mode isn't available.", show_alert=True)
                return
            await self.ctx.db.run(repo.set_engine_preference, user_id, rest)
            engine = self.ctx.registry.get(rest)
            await query.answer(f"{engine.label} mode selected")
            await query.message.reply_text(
                f"✅ <b>{html.escape(engine.label)} mode</b> is on. {html.escape(engine.description)}",
                parse_mode=ParseMode.HTML,
            )

        elif action == "del":
            ids = context.user_data.pop("delete_pending", "missing")
            context.user_data.pop("delete_map", None)
            if rest != "yes" or ids == "missing":
                await query.answer()
                await query.edit_message_text("👍 Cancelled. Your notes are untouched.")
                return
            count = await self.ctx.db.run(repo.delete_notes, user_id, ids)
            await query.answer()
            await query.edit_message_text(f"✅ Deleted {count} note(s) permanently.")
        else:
            await query.answer()

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Telegram handler error: %s", context.error, exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            with contextlib.suppress(TelegramError):
                await update.effective_message.reply_text(
                    "⚠️ Vaani is getting a lot of traffic right now. Please try again in a minute."
                )
