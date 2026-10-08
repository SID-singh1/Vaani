"""Builds the engines this server can offer from its settings."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import Settings
from ..errors import EngineUnavailable
from .base import Engine, LLMClient
from .gemini import GeminiClient
from .groq_asr import GroqSpeechToText
from .llama_server import LlamaServer, LocalLLMClient
from .local_asr import FasterWhisperSpeechToText, OnnxWhisperSpeechToText
from .openai_compat import ChatCompletionsClient

log = logging.getLogger(__name__)

GROQ_OPENAI_BASE = "https://api.groq.com/openai/v1"


class EngineRegistry:
    def __init__(self, engines: dict[str, Engine], default: str, closers=()):
        if not engines:
            log.warning("No processing engine is configured; notes will be rejected.")
        self._engines = engines
        self._closers = list(closers)
        self.default = default if default in engines else next(iter(engines), "")

    def get(self, name: str | None) -> Engine:
        key = name or self.default
        engine = self._engines.get(key)
        if engine is None:
            raise EngineUnavailable(detail=f"engine {key!r} is not available")
        return engine

    def resolve(self, preference: str | None) -> Engine:
        """The user's preferred engine if this server offers it, else the default."""
        if preference in self._engines:
            return self._engines[preference]
        return self.get(None)

    def available(self) -> list[Engine]:
        return list(self._engines.values())

    def has(self, name: str) -> bool:
        return name in self._engines

    async def close(self) -> None:
        for close in self._closers:
            await close()


def _cloud_llms(settings: Settings, http: httpx.AsyncClient) -> list[LLMClient]:
    llms: list[LLMClient] = []
    if settings.gemini_api_key:
        llms += [GeminiClient(settings.gemini_api_key, model, http) for model in settings.gemini_models]
    if settings.groq_api_key:
        for model in settings.groq_llm_models:
            extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
            llms.append(
                ChatCompletionsClient(
                    name=f"groq:{model}",
                    base_url=GROQ_OPENAI_BASE,
                    model=model,
                    http=http,
                    api_key=settings.groq_api_key,
                    extra_body=extra,
                )
            )
    return llms


def build_registry(settings: Settings, http: httpx.AsyncClient) -> EngineRegistry:
    engines: dict[str, Engine] = {}
    closers = []
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    if settings.cloud_configured:
        engines["cloud"] = Engine(
            name="cloud",
            label="Fast",
            description="Groq Whisper large-v3 + Gemini (with Groq fallback). Audio is sent to these AI providers.",
            asr=GroqSpeechToText(
                settings.groq_api_key, settings.groq_asr_model, settings.temp_dir, http, settings.groq_asr_language
            ),
            llms=_cloud_llms(settings, http),
            transliteration="llm",
            concurrency=settings.cloud_concurrency,
            max_audio_seconds=settings.max_audio_seconds_cloud,
            typical_seconds=12,
            tags=["fast", "most accurate"],
        )
    else:
        log.warning("Cloud engine disabled: set GROQ_API_KEY and GEMINI_API_KEY.")

    if settings.private_engine_enabled:
        server = LlamaServer(
            url=settings.llama_server_url,
            binary=settings.llama_server_bin,
            model_path=settings.private_llm_model_path,
            ctx_size=settings.private_llm_ctx,
        )
        if settings.private_asr_backend == "onnx":
            asr = OnnxWhisperSpeechToText(
                Path(settings.private_asr_model), settings.temp_dir, settings.private_asr_language
            )
        else:
            asr = FasterWhisperSpeechToText(
                settings.private_asr_model, settings.models_dir, settings.temp_dir, settings.private_asr_language
            )
        if not server.configured:
            log.warning(
                "Private engine disabled: set LLAMA_SERVER_URL, or LLAMA_SERVER_BIN and PRIVATE_LLM_MODEL_PATH."
            )
        else:
            label = Path(settings.private_llm_model_path).stem if settings.private_llm_model_path else "llama.cpp"
            engines["private"] = Engine(
                name="private",
                label="Private",
                description="Whisper and a local LLM running on this server's CPU. Nothing is sent to AI companies.",
                asr=asr,
                llms=[LocalLLMClient(server, http, label)],
                transliteration="rules",
                concurrency=settings.private_concurrency,
                max_audio_seconds=settings.max_audio_seconds_private,
                typical_seconds=90,
                tags=["private", "slower"],
            )
            closers.append(server.close)

    return EngineRegistry(engines, settings.default_engine, closers)
