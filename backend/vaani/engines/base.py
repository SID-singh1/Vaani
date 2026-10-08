"""Engine interfaces.

An engine pairs a speech-to-text provider with an ordered list of LLM clients. Providers are thin transport
adapters; all prompting and validation lives in vaani.analysis, so swapping a provider
never changes behaviour elsewhere."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Transcription:
    text: str
    model: str
    language: str | None = None
    # Whisper's own quality signals, when the provider exposes them.
    dropped_segments: int = 0


class SpeechToText(Protocol):
    name: str

    async def transcribe(self, audio_path: Path) -> Transcription: ...


class LLMClient(Protocol):
    name: str

    async def generate(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict | None = None,
        max_output_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> str:
        """Return the model's text (a JSON document when json_schema is given).
        Raise vaani.errors.ProviderError on failure."""
        ...


@dataclass
class Engine:
    name: str  # "cloud" | "private"
    label: str
    description: str
    asr: SpeechToText
    llms: list[LLMClient]  # tried in order; the next one takes over when one fails
    transliteration: str  # "llm" | "rules"
    concurrency: int
    max_audio_seconds: int
    typical_seconds: float  # first ETA estimate before real timings exist
    tags: list[str] = field(default_factory=list)
