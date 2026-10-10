"""Lifecycle for a local llama.cpp server (the private engine's LLM).

Either connect to an already-running server (LLAMA_SERVER_URL, e.g. a docker-compose
service) or spawn one from LLAMA_SERVER_BIN + PRIVATE_LLM_MODEL_PATH on first use.
llama-server is used instead of Python bindings: it is the fastest CPU path and exposes an
OpenAI-compatible API with grammar-constrained JSON output.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path

import httpx

from ..errors import EngineUnavailable

log = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LlamaServer:
    def __init__(self, *, url: str = "", binary: str = "", model_path: str = "", ctx_size: int = 8192):
        self.external_url = url
        self.binary = binary
        self.model_path = model_path
        self.ctx_size = ctx_size
        self._process: asyncio.subprocess.Process | None = None
        self._url = url
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        if self.external_url:
            return True
        return bool(self.binary and Path(self.binary).exists() and self.model_path and Path(self.model_path).exists())

    async def base_url(self, http: httpx.AsyncClient) -> str:
        """Return the OpenAI-compatible base URL, starting the server if needed."""
        if self.external_url:
            return self.external_url + "/v1"
        async with self._lock:
            if self._process is None or self._process.returncode is not None:
                await self._spawn(http)
        return self._url + "/v1"

    async def _spawn(self, http: httpx.AsyncClient) -> None:
        port = _free_port()
        threads = str(max(1, (os.cpu_count() or 2)))
        log.info("Starting llama-server (%s) on port %d", Path(self.model_path).name, port)
        self._process = await asyncio.create_subprocess_exec(
            self.binary,
            "-m",
            self.model_path,
            "-c",
            str(self.ctx_size),
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
            "-t",
            threads,
            "-np",
            "1",
            "--no-webui",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._url = f"http://127.0.0.1:{port}"
        for _ in range(240):  # large models can take a while to mmap on slow disks
            if self._process.returncode is not None:
                raise EngineUnavailable(detail=f"llama-server exited with code {self._process.returncode}")
            try:
                if (await http.get(f"{self._url}/health", timeout=2)).status_code == 200:
                    log.info("llama-server ready")
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.5)
        raise EngineUnavailable(detail="llama-server did not become ready in 120s")

    async def close(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                self._process.kill()
        self._process = None


class LocalLLMClient:
    """ChatCompletionsClient bound to a LlamaServer that may not be running yet."""

    def __init__(self, server: LlamaServer, http: httpx.AsyncClient, model_label: str):
        from .openai_compat import ChatCompletionsClient

        self.server = server
        self.http = http
        self.name = f"llama.cpp:{model_label}"
        self._client_cls = ChatCompletionsClient
        self._client = None
        self._base = ""

    async def generate(self, **kwargs) -> str:
        base = await self.server.base_url(self.http)  # (re)starts the server if it died
        if self._client is None or base != self._base:
            # CPU generation is slow: allow long requests and don't retry them.
            self._client = self._client_cls(
                name=self.name, base_url=base, model="local", http=self.http, timeout=900, retries=0
            )
            self._base = base
        return await self._client.generate(**kwargs)
