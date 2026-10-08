"""Provider adapters against mocked HTTP: request shape, retries, failure classification."""

import json

import httpx
import pytest

from conftest import make_wav, requires_ffmpeg
from vaani.engines.gemini import GeminiClient, thinking_config
from vaani.engines.groq_asr import GroqSpeechToText, join_segments
from vaani.engines.openai_compat import ChatCompletionsClient
from vaani.errors import ProviderError

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr("vaani.engines.http.asyncio.sleep", instant)


def mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def gemini_ok(text: str, finish: str = "STOP") -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]})


class TestGemini:
    async def test_request_shape_and_key_in_header(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["url"] = str(request.url)
            seen["key"] = request.headers.get("x-goog-api-key")
            seen["body"] = json.loads(request.content)
            return gemini_ok('{"ok": true}')

        client = GeminiClient("secret-key", "gemini-2.5-flash", mock_http(handler))
        out = await client.generate(system="sys", user="hi", json_schema={"type": "object"})
        assert out == '{"ok": true}'
        assert "secret-key" not in seen["url"] and seen["key"] == "secret-key"
        config = seen["body"]["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"] == {"type": "object"}
        assert config["thinkingConfig"] == {"thinkingBudget": 0}
        assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"

    async def test_retries_transient_errors(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            return (
                httpx.Response(503, json={"error": {"message": "high demand"}}) if len(attempts) < 2 else gemini_ok("x")
            )

        assert await GeminiClient("k", "gemini-2.5-flash", mock_http(handler)).generate(system="s", user="u") == "x"
        assert len(attempts) == 2

    async def test_client_errors_are_not_retried(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            return httpx.Response(404, json={"error": {"message": "model not found"}})

        with pytest.raises(ProviderError) as info:
            await GeminiClient("k", "gemini-old", mock_http(handler)).generate(system="s", user="u")
        assert len(attempts) == 1 and not info.value.retryable

    async def test_truncated_output_is_an_error(self):
        client = GeminiClient("k", "gemini-2.5-flash", mock_http(lambda r: gemini_ok('{"a":', "MAX_TOKENS")))
        with pytest.raises(ProviderError, match="truncated"):
            await client.generate(system="s", user="u")

    async def test_unsupported_thinking_config_is_dropped_and_retried(self):
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            if "thinkingConfig" in body["generationConfig"]:
                return httpx.Response(400, json={"error": {"message": "Thinking level LOW is not supported"}})
            return gemini_ok("fine")

        assert await GeminiClient("k", "gemini-3.8-flash", mock_http(handler)).generate(system="s", user="u") == "fine"
        assert "thinkingConfig" not in bodies[-1]["generationConfig"]

    def test_thinking_config_by_model_family(self):
        assert thinking_config("gemini-2.5-flash") == {"thinkingBudget": 0}
        assert thinking_config("gemini-3.8-flash") == {"thinkingLevel": "low"}
        assert thinking_config("gemini-3.5-flash-lite") is None


class TestChatCompletions:
    async def test_json_schema_then_json_object_downgrade(self):
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            if body["response_format"]["type"] == "json_schema":
                return httpx.Response(400, json={"error": {"message": "response_format json_schema unsupported"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

        client = ChatCompletionsClient(
            name="groq:x",
            base_url="https://api.example/v1",
            model="x",
            http=mock_http(handler),
            api_key="k",
            extra_body={"reasoning_effort": "low"},
        )
        assert await client.generate(system="s", user="u", json_schema={"type": "object"}) == "{}"
        assert bodies[-1]["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in bodies[-1]["messages"][0]["content"]
        assert bodies[-1]["reasoning_effort"] == "low"

    async def test_length_finish_is_truncation(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]})

        client = ChatCompletionsClient(name="x", base_url="http://x/v1", model="m", http=mock_http(handler))
        with pytest.raises(ProviderError, match="truncated"):
            await client.generate(system="s", user="u")

    async def test_long_rate_limit_fails_fast(self):
        def handler(request):
            return httpx.Response(429, headers={"retry-after": "3600"}, json={"error": "daily quota"})

        client = ChatCompletionsClient(name="x", base_url="http://x/v1", model="m", http=mock_http(handler))
        with pytest.raises(ProviderError, match="rate limited"):
            await client.generate(system="s", user="u")


class TestGroqASR:
    def test_drops_short_silent_segments_only(self):
        payload = {
            "segments": [
                {"text": " Kal meeting hai.", "no_speech_prob": 0.01, "avg_logprob": -0.2},
                {"text": " Thank you.", "no_speech_prob": 0.92, "avg_logprob": -1.4},
                {
                    "text": " Low confidence but clearly speech, kept because only one signal fires.",
                    "no_speech_prob": 0.95,
                    "avg_logprob": -0.3,
                },
            ]
        }
        text, dropped = join_segments(payload)
        assert dropped == 1 and "Thank you" not in text and "Kal meeting hai." in text

    def test_falls_back_to_text_without_segments(self):
        assert join_segments({"text": " hello "}) == ("hello", 0)

    @requires_ffmpeg
    async def test_uploads_16k_wav_and_cleans_up(self, tmp_path):
        seen = {}

        def handler(request: httpx.Request):
            seen["auth"] = request.headers["authorization"]
            seen["body"] = request.content
            return httpx.Response(200, json={"text": "kal meeting hai", "language": "hindi"})

        asr = GroqSpeechToText("gk", "whisper-large-v3", tmp_path, mock_http(handler))
        result = await asr.transcribe(make_wav(tmp_path / "in.wav", seconds=1.5, rate=44100))
        assert result.text == "kal meeting hai" and result.model == "groq:whisper-large-v3"
        assert seen["auth"] == "Bearer gk"
        assert b'name="response_format"' in seen["body"] and b"verbose_json" in seen["body"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["in.wav"]  # converted temp file removed
