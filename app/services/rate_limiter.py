"""Per-domain request rate limiting."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse


class RateLimiter:
    def __init__(self) -> None:
        self._last_request: dict[str, float] = {}
        self._window_counts: dict[str, list[float]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def wait_or_reject(self, url: str, source) -> tuple[bool, str]:
        domain = self._domain(url)
        async with self._locks[domain]:
            now = time.monotonic()
            delay = float(getattr(source, "default_crawl_delay_seconds", 10) or 10)
            max_rpm = int(getattr(source, "max_requests_per_minute", 6) or 6)

            last = self._last_request.get(domain, 0.0)
            if now - last < delay:
                await asyncio.sleep(delay - (now - last))

            cutoff = time.monotonic() - 60.0
            self._window_counts[domain] = [t for t in self._window_counts[domain] if t >= cutoff]
            if len(self._window_counts[domain]) >= max_rpm:
                return False, "rate_limited"

            self._window_counts[domain].append(time.monotonic())
            self._last_request[domain] = time.monotonic()
            return True, "ok"
