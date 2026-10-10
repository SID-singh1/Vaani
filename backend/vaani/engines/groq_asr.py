"""Groq-hosted Whisper (OpenAI-compatible transcription endpoint)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

from ..audio import to_mp3_64k, to_wav_16k
from ..errors import ProviderError
from .base import Transcription
from .http import send_with_retries

log = logging.getLogger(__name__)

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MAX_UPLOAD = 24 * 1024 * 1024  # Groq's limit is 25 MB; keep a margin

# Whisper's reference implementation treats a segment as silence when it is both likely
# non-speech and low-confidence. Hallucinated phrases on pauses ("Thank you for watching")
# match this pattern; we only drop short segments to stay conservative with real speech.
NO_SPEECH_PROB = 0.7
AVG_LOGPROB = -1.0
MAX_DROPPED_CHARS = 80


def join_segments(payload: dict) -> tuple[str, int]:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return (payload.get("text") or "").strip(), 0
    kept, dropped = [], 0
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        silent = (seg.get("no_speech_prob") or 0) > NO_SPEECH_PROB and (seg.get("avg_logprob") or 0) < AVG_LOGPROB
        if silent and len(text) <= MAX_DROPPED_CHARS:
            dropped += 1
            continue
        kept.append(text)
    return " ".join(kept).strip(), dropped


class GroqSpeechToText:
    def __init__(self, api_key: str, model: str, temp_dir: Path, http: httpx.AsyncClient, language: str = ""):
        self.api_key = api_key
        self.model = model
        self.language = language
        self.temp_dir = temp_dir
        self.http = http
        self.name = f"groq:{model}"

    async def transcribe(self, audio_path: Path) -> Transcription:
        stem = self.temp_dir / f"{uuid.uuid4().hex}"
        wav = Path(f"{stem}.wav")
        mp3 = Path(f"{stem}.mp3")
        try:
            await to_wav_16k(audio_path, wav)
            upload, mime = wav, "audio/wav"
            if wav.stat().st_size > GROQ_MAX_UPLOAD:
                # 16 kHz PCM fills 24 MB in ~12 minutes; 64 kbps MP3 fits ~50 minutes.
                upload, mime = await to_mp3_64k(wav, mp3), "audio/mpeg"

            data = {"model": self.model, "temperature": "0", "response_format": "verbose_json"}
            if self.language:
                data["language"] = self.language

            async def send() -> httpx.Response:
                with upload.open("rb") as fh:
                    return await self.http.post(
                        GROQ_TRANSCRIPTIONS_URL,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data=data,
                        files={"file": (upload.name, fh, mime)},
                        timeout=httpx.Timeout(300, connect=15),
                    )

            response = await send_with_retries(self.name, send)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(detail=f"{self.name}: non-JSON response") from exc
            text, dropped = join_segments(payload)
            if dropped:
                log.info("%s: dropped %d silent segment(s)", self.name, dropped)
            return Transcription(text=text, model=self.name, language=payload.get("language"), dropped_segments=dropped)
        finally:
            for path in (wav, mp3):
                path.unlink(missing_ok=True)
