"""Shared HTTP retry logic for provider calls, with quota-aware cooldowns.

Free API tiers have small per-model quotas (Gemini 2.5 Flash allows ~20 requests a day),
and every retry counts against them. So:
  * transient failures (timeouts, 5xx, short 429s) are retried with backoff;
  * a quota error is never retried. The provider is put on cooldown instead, and calls
    to it fail instantly (no request sent) until the cooldown ends, so the analyzer moves
    straight to the next model in the chain.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable

import httpx

from ..errors import ProviderError

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
DAILY_QUOTA_COOLDOWN = 3600.0  # re-probe an exhausted daily quota once an hour
_RETRY_DELAY_IN_BODY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')

_cooldowns: dict[str, float] = {}


def cooling_down(name: str) -> float:
    """Seconds left on this provider's cooldown (0 if available)."""
    return max(0.0, _cooldowns.get(name, 0.0) - time.monotonic())


def cool_down(name: str, seconds: float) -> None:
    _cooldowns[name] = max(_cooldowns.get(name, 0.0), time.monotonic() + seconds)
    log.warning("%s: rate limited, skipping it for %.0fs", name, seconds)


def reset_cooldowns() -> None:
    _cooldowns.clear()


def _wait_hint(response: httpx.Response) -> float | None:
    """Retry delay from the Retry-After header (Groq/OpenAI) or the error body (Gemini)."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY_DELAY_IN_BODY.search(response.text)
    return float(match.group(1)) if match else None


def _is_daily_quota(response: httpx.Response) -> bool:
    text = response.text
    return "PerDay" in text or "per day" in text.lower() or "RPD" in text


async def send_with_retries(
    name: str,
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int = 2,
    max_wait: float = 8.0,
) -> httpx.Response:
    """Call `send` until it returns a 2xx response, or raise ProviderError."""
    remaining = cooling_down(name)
    if remaining:
        raise ProviderError(detail=f"{name} skipped: rate-limit cooldown ({remaining:.0f}s left)")

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
            if response.status_code == 429:
                wait = _wait_hint(response)
                if _is_daily_quota(response):
                    cool_down(name, DAILY_QUOTA_COOLDOWN)
                    raise ProviderError(detail=f"{name} daily quota exhausted: {body}")
                if wait is not None and wait > max_wait:
                    cool_down(name, wait)
                    raise ProviderError(detail=f"{name} rate limited for {wait:.0f}s: {body}")
            last = ProviderError(detail=f"{name} HTTP {response.status_code}: {body}")
            log.warning("%s: HTTP %d (attempt %d)", name, response.status_code, attempt + 1)
        if attempt < retries:
            await asyncio.sleep(min(max_wait, (2**attempt) + random.uniform(0, 0.5)))
    if isinstance(last, ProviderError):
        raise last
    raise ProviderError(detail=f"{name} failed after {retries + 1} attempts: {last!r}")
