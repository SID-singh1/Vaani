"""Shared HTTP retry logic for provider calls."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

from ..errors import ProviderError

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def send_with_retries(
    name: str,
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int = 2,
    max_wait: float = 8.0,
) -> httpx.Response:
    """Call `send` until it returns a 2xx response.

    Transient failures (timeouts, connection errors, 429/5xx) are retried with jittered
    exponential backoff, honouring Retry-After up to `max_wait`. A 429 that would need a
    longer wait (a per-day quota, typically) is not worth blocking a user for: it fails
    fast so the caller can fail over to another provider.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await send()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            log.warning("%s: %s (attempt %d)", name, type(exc).__name__, attempt + 1)
        else:
            if response.is_success:
                return response
            body = response.text[:300]
            if response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(detail=f"{name} HTTP {response.status_code}: {body}", retryable=False)
            wait = _retry_after(response)
            if wait is not None and wait > max_wait:
                raise ProviderError(detail=f"{name} rate limited for {wait:.0f}s: {body}")
            last = ProviderError(detail=f"{name} HTTP {response.status_code}: {body}")
            log.warning("%s: HTTP %d (attempt %d)", name, response.status_code, attempt + 1)
        if attempt < retries:
            await asyncio.sleep(min(max_wait, (2**attempt) + random.uniform(0, 0.5)))
    if isinstance(last, ProviderError):
        raise last
    raise ProviderError(detail=f"{name} failed after {retries + 1} attempts: {last!r}")
