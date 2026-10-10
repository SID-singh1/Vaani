"""Google Gemini via the generateContent REST endpoint."""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from .http import send_with_retries

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
BLOCKED_FINISH_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"}


def thinking_config(model: str) -> dict | None:
    """Keep thinking minimal: these are extraction tasks, and thinking tokens add latency
    and count against the output budget. The control differs by model generation."""
    if "2.5-flash" in model and "lite" not in model:
        return {"thinkingBudget": 0}
    if model.startswith("gemini-3") and "lite" not in model:
        return {"thinkingLevel": "low"}
    return None


class GeminiClient:
    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient):
        self.api_key = api_key
        self.model = model
        self.http = http
        self.name = f"gemini:{model}"

    async def generate(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict | None = None,
        max_output_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> str:
        config: dict = {"temperature": temperature, "maxOutputTokens": max_output_tokens}
        if json_schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = json_schema
        thinking = thinking_config(self.model)
        if thinking:
            config["thinkingConfig"] = thinking
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }

        async def send() -> httpx.Response:
            return await self.http.post(
                GEMINI_URL.format(model=self.model),
                # Header, not ?key=, so the key never appears in URLs or access logs.
                headers={"x-goog-api-key": self.api_key},
                json=body,
                timeout=httpx.Timeout(120, connect=15),
            )

        try:
            response = await send_with_retries(self.name, send)
        except ProviderError as exc:
            if thinking and "thinking" in exc.detail.lower():
                # A model generation that doesn't accept this thinking control: retry without it.
                config.pop("thinkingConfig", None)
                response = await send_with_retries(self.name, send)
            else:
                raise
        return self._extract_text(response.json())

    def _extract_text(self, data: dict) -> str:
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise ProviderError(detail=f"{self.name}: prompt blocked ({feedback['blockReason']})", retryable=False)
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(detail=f"{self.name}: no candidates returned")
        candidate = candidates[0]
        finish = candidate.get("finishReason", "")
        if finish == "MAX_TOKENS":
            raise ProviderError(detail=f"{self.name}: output truncated at max tokens", retryable=False)
        if finish in BLOCKED_FINISH_REASONS:
            raise ProviderError(detail=f"{self.name}: finished with {finish}", retryable=False)
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise ProviderError(detail=f"{self.name}: empty response")
        return text
