"""Shared fixtures. Tests never call real AI providers: engines are replaced with fakes,
so the suite is fast, free and deterministic."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaani.config import Settings
from vaani.engines.base import Engine, Transcription
from vaani.engines.registry import EngineRegistry
from vaani.errors import ProviderError
from vaani.main import create_app

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


class FakeASR:
    def __init__(self, text: str = "Kal subah team meeting hai, Rahul slides bana dena."):
        self.text = text
        self.name = "fake-asr"
        self.calls = 0

    async def transcribe(self, audio_path: Path) -> Transcription:
        self.calls += 1
        return Transcription(text=self.text, model=self.name)


class FakeLLM:
    """Answers analysis prompts with a fixed note; echoes text for transliteration prompts."""

    def __init__(
        self, name: str = "fake-llm", *, fail: bool = False, analysis: dict | None = None, hinglish: str | None = None
    ):
        self.name = name
        self.fail = fail
        self.calls: list[dict] = []
        self.analysis = analysis or {
            "title": "Team meeting tomorrow",
            "summary": "A team meeting is planned for tomorrow morning.",
            "action_items": [{"task": "Prepare the slides", "owner": "Rahul", "due": "tomorrow morning"}],
            "sentiment": "Neutral",
        }
        self.hinglish = hinglish

    async def generate(self, *, system, user, json_schema=None, max_output_tokens=2048, temperature=0.1):
        self.calls.append({"system": system, "user": user, "schema": json_schema})
        if self.fail:
            raise ProviderError(detail=f"{self.name} is down")
        if json_schema is None:
            return self.hinglish if self.hinglish is not None else user
        data = dict(self.analysis)
        if "hinglish_transcript" in json_schema.get("properties", {}):
            data["hinglish_transcript"] = self.hinglish or "kal subah meeting hai"
        return json.dumps(data)


def fake_registry(asr: FakeASR | None = None, llms: list | None = None, *, private: bool = False):
    def factory(settings: Settings, http) -> EngineRegistry:
        engines = {
            "cloud": Engine(
                name="cloud",
                label="Fast",
                description="Test cloud engine.",
                asr=asr or FakeASR(),
                llms=llms or [FakeLLM()],
                transliteration="llm",
                concurrency=2,
                max_audio_seconds=600,
                typical_seconds=1,
            )
        }
        if private:
            engines["private"] = Engine(
                name="private",
                label="Private",
                description="Test private engine.",
                asr=FakeASR(),
                llms=[FakeLLM("fake-local")],
                transliteration="rules",
                concurrency=1,
                max_audio_seconds=300,
                typical_seconds=1,
            )
        return EngineRegistry(engines, "cloud")

    return factory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        secret_key="test-secret-key",
        admin_secret_key="admin-key",
        temp_dir=tmp_path / "temp",
        models_dir=tmp_path / "models",
        groq_api_key="x",
        gemini_api_key="x",
        user_daily_note_limit=20,
        user_burst_limit=50,
        global_daily_cloud_limit=0,
        ip_rate_limit="1000/minute",
        session_rate_limit="1000/minute",
        telegram_mode="disabled",
    )


@pytest.fixture
def make_client(settings):
    clients = []

    def _make(registry_factory=None, **overrides) -> TestClient:
        app = create_app(replace(settings, **overrides), registry_factory or fake_registry())
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def auth_headers(client: TestClient, legacy_user_id: str | None = None) -> dict:
    body = {"legacy_user_id": legacy_user_id} if legacy_user_id else None
    token = client.post("/api/v1/session", json=body).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def make_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    """A short sine tone: valid audio that ffmpeg can decode."""
    import math

    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return path


def wait_for_note(client: TestClient, headers: dict, note_id: str, attempts: int = 50) -> dict:
    for _ in range(attempts):
        note = client.get(f"/api/v1/notes/{note_id}?wait=2", headers=headers).json()
        if note["status"] in ("done", "failed"):
            return note
    raise AssertionError(f"note {note_id} did not finish: {note}")


def ffmpeg_version() -> str:
    return subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _fresh_provider_cooldowns():
    from vaani.engines.http import reset_cooldowns

    reset_cooldowns()
    yield
    reset_cooldowns()
