"""Formatting shared by the chat channels."""

from __future__ import annotations

import html
from dataclasses import dataclass

SENTIMENT_EMOJI = {"Positive": "🙂", "Neutral": "😐", "Negative": "🙁"}
STAGE_TEXT = {
    "queued": "⏳ Queued",
    "transcribing": "🎧 Transcribing your note…",
    "analyzing": "🧠 Writing the summary and action items…",
}
MIN_TEXT_NOTE_WORDS = 6


@dataclass
class NoteView:
    """The parts of a finished note that chat channels display."""

    id: str
    title: str
    summary: str
    action_items: list[dict]
    sentiment: str
    transcript: str
    engine: str | None = None


def queued_text(position: int | None, eta_seconds: int | None) -> str:
    if position and position > 1:
        return f"⏳ Queued (#{position}, about {max(5, eta_seconds or 0)}s). I'll reply here when it's ready."
    return "⏳ Got it! Working on your note…"


def _item_line(item: dict, *, bold, italic, escape) -> str:
    line = f"• {escape(item['task'])}"
    extras = []
    if item.get("owner"):
        extras.append(f"👤 {escape(item['owner'])}")
    if item.get("due"):
        extras.append(f"⏰ {escape(item['due'])}")
    if extras:
        line += " " + italic("(" + ", ".join(extras) + ")")
    return line


def format_note_html(note: NoteView) -> str:
    def bold(s):
        return f"<b>{s}</b>"

    def italic(s):
        return f"<i>{s}</i>"

    esc = html.escape
    emoji = SENTIMENT_EMOJI.get(note.sentiment, "")
    lines = [
        bold(f"📝 {esc(note.title)}"),
        "",
        f"{bold('Summary')} {emoji}",
        esc(note.summary),
        "",
        bold("✅ Action items"),
    ]
    if note.action_items:
        lines += [_item_line(i, bold=bold, italic=italic, escape=esc) for i in note.action_items]
    else:
        lines.append("• Nothing to do here.")
    return "\n".join(lines)


def format_note_whatsapp(note: NoteView) -> str:
    # WhatsApp formatting has no escaping, so strip the markers from model text.
    def esc(s: str) -> str:
        return s.replace("*", "").replace("_", " ").replace("~", "").replace("`", "")

    emoji = SENTIMENT_EMOJI.get(note.sentiment, "")
    lines = [f"*📝 {esc(note.title)}*", "", f"*Summary* {emoji}", esc(note.summary), "", "*✅ Action items*"]
    if note.action_items:
        lines += [
            _item_line(i, bold=lambda s: f"*{s}*", italic=lambda s: f"_{s}_", escape=esc) for i in note.action_items
        ]
    else:
        lines.append("• Nothing to do here.")
    return "\n".join(lines)


def split_text(text: str, limit: int) -> list[str]:
    """Split on line (then word) boundaries so no chunk exceeds `limit` characters."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:cut])
            line = line[cut:].lstrip()
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]
