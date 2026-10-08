"""OpenAI-compatible chat completions: Groq's hosted LLMs and the local llama.cpp server."""

from __future__ import annotations

import json

import httpx

from ..errors import ProviderError
from .http import send_with_retries


class ChatCompletionsClient:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        http: httpx.AsyncClient,
        api_key: str = "",
        extra_body: dict | None = None,
        timeout: float = 120,
        retries: int = 2,
    ):
        self.name = name
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.http = http
        self.api_key = api_key
        self.extra_body = extra_body or {}
        self.timeout = timeout
        self.retries = retries
        self._schema_mode = "json_schema"  # downgraded to json_object if the server rejects it

    async def generate(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict | None = None,
        max_output_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> str:
        try:
            return await self._generate(system, user, json_schema, max_output_tokens, temperature)
        except ProviderError as exc:
            if json_schema is not None and self._schema_mode == "json_schema" and "response_format" in exc.detail:
                self._schema_mode = "json_object"
                return await self._generate(system, user, json_schema, max_output_tokens, temperature)
            raise

    async def _generate(self, system, user, json_schema, max_output_tokens, temperature) -> str:
        if json_schema is not None and self._schema_mode == "json_object":
            system = (
                f"{system}\n\nRespond with a single JSON object matching this JSON Schema:\n{json.dumps(json_schema)}"
            )
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            **self.extra_body,
        }
        if json_schema is not None:
            if self._schema_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": json_schema},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        async def send() -> httpx.Response:
            return await self.http.post(
                self.url, headers=headers, json=body, timeout=httpx.Timeout(self.timeout, connect=15)
            )

        response = await send_with_retries(self.name, send, retries=self.retries)
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError(detail=f"{self.name}: malformed response") from exc
        if choice.get("finish_reason") == "length":
            raise ProviderError(detail=f"{self.name}: output truncated at max tokens", retryable=False)
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise ProviderError(detail=f"{self.name}: empty response")
        return content
