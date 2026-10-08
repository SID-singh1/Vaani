"""One note, end to end: audio -> transcript -> Hinglish transcript + structured notes."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .analysis import NoteAnalyzer
from .audio import probe_duration
from .engines.registry import EngineRegistry
from .errors import AudioTooLong, NoSpeechDetected
from .text.cleanup import clean_transcript

log = logging.getLogger(__name__)

StageCallback = Callable[[str], Awaitable[None]]


@dataclass
class NoteRequest:
    note_id: str
    user_id: str
    engine: str
    source: str  # audio | text
    audio_path: Path | None = None
    text: str | None = None


@dataclass
class NoteResult:
    transcript: str
    title: str
    summary: str
    action_items: list[dict]
    sentiment: str
    audio_duration_sec: float | None
    models: str
    transliteration: str
    asr_ms: int | None
    llm_ms: int
    total_ms: int

    def as_record(self) -> dict:
        return self.__dict__.copy()


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class NotePipeline:
    def __init__(self, registry: EngineRegistry, *, log_transcripts: bool = False):
        self.registry = registry
        self.log_transcripts = log_transcripts

    async def run(self, request: NoteRequest, on_stage: StageCallback) -> NoteResult:
        engine = self.registry.get(request.engine)
        started = time.perf_counter()
        models: list[str] = []
        duration: float | None = None
        asr_ms: int | None = None

        if request.source == "audio":
            assert request.audio_path is not None
            duration = await probe_duration(request.audio_path)
            if duration is not None and duration > engine.max_audio_seconds:
                minutes = engine.max_audio_seconds // 60
                raise AudioTooLong(
                    f"That recording is {duration / 60:.0f} minutes long. "
                    f"{engine.label} mode handles up to {minutes} minutes per note."
                )
            await on_stage("transcribing")
            asr_started = time.perf_counter()
            transcription = await engine.asr.transcribe(request.audio_path)
            asr_ms = _ms(asr_started)
            models.append(transcription.model)
            text = clean_transcript(transcription.text)
            if sum(ch.isalpha() for ch in text) < 2:
                raise NoSpeechDetected()
        else:
            text = (request.text or "").strip()

        if self.log_transcripts:
            log.info("note %s raw transcript: %s", request.note_id, text)
        else:
            log.info("note %s transcript: %d chars", request.note_id, len(text))

        await on_stage("analyzing")
        llm_started = time.perf_counter()
        analyzer = NoteAnalyzer(engine.llms, engine.transliteration)
        outcome = await analyzer.process(text)
        llm_ms = _ms(llm_started)
        models.extend(outcome.models)

        return NoteResult(
            transcript=outcome.transcript,
            title=outcome.analysis.title,
            summary=outcome.analysis.summary,
            action_items=outcome.analysis.action_items_dicts(),
            sentiment=outcome.analysis.sentiment,
            audio_duration_sec=round(duration, 2) if duration else None,
            models=" + ".join(models),
            transliteration=outcome.transliteration,
            asr_ms=asr_ms,
            llm_ms=llm_ms,
            total_ms=_ms(started),
        )
