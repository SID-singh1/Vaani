"""On-device Whisper for the private engine. Audio never leaves the server.

Two CPU backends, both INT8:
  * faster-whisper (CTranslate2): default. Built-in VAD skips silence, which removes most
    hallucinations at the source, and it needs no PyTorch.
  * onnx (optimum + ONNX Runtime): the original v1 path, using the model exported and
    quantized by ml/export/export_whisper.py.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path

from ..audio import to_wav_16k
from .base import Transcription

log = logging.getLogger(__name__)


class FasterWhisperSpeechToText:
    def __init__(self, model: str, models_dir: Path, temp_dir: Path, language: str = "hi", cpu_threads: int = 0):
        self.model_ref = model
        self.models_dir = models_dir
        self.temp_dir = temp_dir
        self.language = language or None
        self.cpu_threads = cpu_threads
        self.name = f"faster-whisper:{Path(model).name}-int8"
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Loading faster-whisper %s (int8)...", self.model_ref)
            self._model = WhisperModel(
                self.model_ref,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.cpu_threads,
                download_root=str(self.models_dir / "faster-whisper"),
            )
        return self._model

    def _run(self, wav: Path) -> Transcription:
        with self._lock:  # one inference at a time per model; it already uses every core
            model = self._load()
            segments, info = model.transcribe(
                str(wav),
                language=self.language,
                task="transcribe",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,  # stops one bad segment from looping into the next
            )
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return Transcription(text=text, model=self.name, language=info.language)

    async def transcribe(self, audio_path: Path) -> Transcription:
        wav = self.temp_dir / f"{uuid.uuid4().hex}.wav"
        try:
            await to_wav_16k(audio_path, wav)
            return await asyncio.to_thread(self._run, wav)
        finally:
            wav.unlink(missing_ok=True)


class OnnxWhisperSpeechToText:
    def __init__(self, model_dir: Path, temp_dir: Path, language: str = "hi"):
        self.model_dir = model_dir
        self.temp_dir = temp_dir
        self.language = language or None
        self.name = f"onnx:{model_dir.name}"
        self._pipeline = None
        self._lock = threading.Lock()

    def _load(self):
        if self._pipeline is None:
            from optimum.onnxruntime import ORTModelForSpeechSeq2Seq
            from transformers import AutoProcessor, pipeline

            log.info("Loading ONNX Whisper from %s...", self.model_dir)
            processor = AutoProcessor.from_pretrained(self.model_dir)
            model = ORTModelForSpeechSeq2Seq.from_pretrained(self.model_dir)
            self._pipeline = pipeline(
                "automatic-speech-recognition",
                model=model,
                tokenizer=processor.tokenizer,
                feature_extractor=processor.feature_extractor,
                chunk_length_s=30,
            )
        return self._pipeline

    def _run(self, wav: Path) -> Transcription:
        import soundfile as sf

        with self._lock:
            asr = self._load()
            speech, _ = sf.read(str(wav), dtype="float32")
            kwargs = {"task": "transcribe"}
            if self.language:
                kwargs["language"] = self.language
            result = asr(speech, generate_kwargs=kwargs)
        return Transcription(text=result["text"].strip(), model=self.name, language=self.language)

    async def transcribe(self, audio_path: Path) -> Transcription:
        wav = self.temp_dir / f"{uuid.uuid4().hex}.wav"
        try:
            await to_wav_16k(audio_path, wav)
            return await asyncio.to_thread(self._run, wav)
        finally:
            wav.unlink(missing_ok=True)
