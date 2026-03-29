# cache.py
"""
In-memory cache with domain-aware TTLs using aiocache.
Single-line upgrade to Redis: change Cache.MEMORY → Cache.REDIS and provide endpoint.
"""

import hashlib
import logging
from typing import Optional

from aiocache import Cache
from aiocache.serializers import JsonSerializer

logger = logging.getLogger(__name__)


class CrawlCache:
    def __init__(self):
        # ─── Swap to Redis later with zero other changes: ───────────────────
        # Cache(Cache.REDIS, endpoint="your-redis-host", port=6379,
        #       serializer=JsonSerializer(), namespace="crawl")
        # ────────────────────────────────────────────────────────────────────
        self._cache = Cache(
            Cache.MEMORY,
            serializer=JsonSerializer(),
            namespace="crawl",
        )

        # Domain-aware TTLs (seconds)
        self.ttl_map: dict[str, int] = {
            "sec.gov":          86400,   # SEC filings        — 24 hours
            "earningswhispers": 3600,    # Earnings calendar  — 1 hour
            "finance.yahoo":    300,     # Yahoo quotes       — 5 minutes
            "marketwatch":      300,     # Market data        — 5 minutes
            "seekingalpha":     1800,    # News articles      — 30 minutes
            "wsj.com":          600,     # WSJ                — 10 minutes
            "reuters.com":      600,     # Reuters            — 10 minutes
            "default":          600,     # Everything else    — 10 minutes
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _ttl_for_url(self, url: str) -> int:
        for domain, ttl in self.ttl_map.items():
            if domain in url:
                return ttl
        return self.ttl_map["default"]

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    # ── Public API ───────────────────────────────────────────────────────────

    async def get(self, url: str) -> Optional[dict]:
        result = await self._cache.get(self._cache_key(url))
        if result is not None:
            result["cache_hit"] = True
            logger.debug("CACHE HIT  %s", url)
            return result
        logger.debug("CACHE MISS %s", url)
        return None

    async def set(self, url: str, content: dict):
        ttl = self._ttl_for_url(url)
        content_copy = {k: v for k, v in content.items() if k != "cache_hit"}
        content_copy["cache_hit"] = False
        await self._cache.set(self._cache_key(url), content_copy, ttl=ttl)
        logger.debug("CACHE SET  %s (ttl=%ds)", url, ttl)

    async def invalidate(self, url: str):
        await self._cache.delete(self._cache_key(url))
        logger.debug("CACHE DEL  %s", url)

    async def clear_all(self):
        await self._cache.clear(namespace="crawl")
        logger.info("Cache cleared (all namespaces).")


# Module-level singleton
cache = CrawlCache()
