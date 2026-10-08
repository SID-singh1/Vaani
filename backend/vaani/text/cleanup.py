"""Clean up known Whisper failure modes in raw transcripts."""

from __future__ import annotations

import re

# Phrases Whisper invents on silence or trailing noise (learned from subtitle-heavy training data).
_HALLUCINATIONS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bthank\s+you\s+(?:very\s+much\s+)?for\s+watching\b[.!?,]*",
        r"\bthanks\s+for\s+watching\b[.!?,]*",
        r"\bplease\s+(?:like\s+and\s+)?subscribe\b[.!?,]*",
        r"\bsubscribe\s+to\s+(?:our|my|the)\s+channel\b[.!?,]*",
        r"\bsubtitles\s+by\b.*$",
        r"\bwatching\b\s*$",
    )
]

_WORD = r"[A-Za-zऀ-ॿ]+"
# 2-9 word phrase repeated back-to-back (autoregressive loop).
_PHRASE_LOOP = re.compile(rf"\b({_WORD}(?:[,\s]+{_WORD}){{1,8}})(?:[,\s]+\1\b)+", re.IGNORECASE)
# One word repeated 3+ times. Two repeats are left alone: Hindi reduplication
# ('kabhi kabhi', 'dheere dheere') is grammatical.
_WORD_LOOP = re.compile(rf"\b({_WORD})(?:[,\s]+\1\b){{2,}}", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def collapse_repetitions(text: str) -> str:
    # Single-word runs first, so "theek theek theek theek" becomes "theek" rather than
    # being read as the two-word phrase "theek theek" repeated.
    text = _WORD_LOOP.sub(r"\1", text)
    for _ in range(3):
        text = _PHRASE_LOOP.sub(r"\1", text)
    text = _WORD_LOOP.sub(r"\1", text)
    return _SPACES.sub(" ", text).strip()


def clean_transcript(text: str) -> str:
    if not text:
        return ""
    for pattern in _HALLUCINATIONS:
        text = pattern.sub("", text)
    return collapse_repetitions(text)
