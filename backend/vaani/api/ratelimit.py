"""Per-IP sliding-window rate limits for unauthenticated or abuse-prone endpoints.

Per-user quotas (vaani.quotas) are the main guard; this stops a single client from
minting sessions or hammering uploads. In-memory is enough for a single-process service.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import Request

from ..errors import VaaniError

_PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


class RateLimited(VaaniError):
    status_code = 429
    code = "rate_limited"
    user_message = "Too many requests. Please slow down and try again in a minute."


def parse_limit(spec: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(second|minute|hour|day)\s*", spec)
    if not match:
        raise ValueError(f"invalid rate limit {spec!r}, expected e.g. '30/minute'")
    return int(match.group(1)), _PERIODS[match.group(2)]


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The client address as seen by the outermost trusted proxy.

    Proxies append to X-Forwarded-For, so with N trusted hops the client's real address is
    the Nth entry from the right. Entries further left are client-controlled and spoofable.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if trusted_proxy_hops > 0 and forwarded:
        entries = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(entries) >= trusted_proxy_hops:
            return entries[-trusted_proxy_hops]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, limit: int, period_seconds: int):
        self.limit = limit
        self.period = period_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        window = self._hits[key]
        while window and window[0] <= now - self.period:
            window.popleft()
        if len(window) >= self.limit:
            raise RateLimited()
        window.append(now)
        if len(self._hits) > 50_000:  # drop idle clients so memory stays bounded
            cutoff = now - self.period
            for client in list(self._hits):
                if not self._hits[client] or self._hits[client][-1] <= cutoff:
                    del self._hits[client]
