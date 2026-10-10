"""Script detection and chunking for code-mixed text."""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")  # Urdu output from Whisper
_SENTENCE_END = re.compile(r"(?<=[.!?।॥۔])\s+|\n+")


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text))


def needs_transliteration(text: str) -> bool:
    """True if the text contains non-Latin script that users can't read as Hinglish."""
    return has_devanagari(text) or has_arabic(text)


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text) if part and part.strip()]


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars` (a single over-long
    sentence is split on word boundaries). Joining the chunks with spaces restores the text."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        pieces = [sentence]
        if len(sentence) > max_chars:
            pieces, buf = [], ""
            for word in sentence.split():
                if buf and len(buf) + 1 + len(word) > max_chars:
                    pieces.append(buf)
                    buf = word
                else:
                    buf = f"{buf} {word}" if buf else word
            if buf:
                pieces.append(buf)
        for piece in pieces:
            if current and len(current) + 1 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks
