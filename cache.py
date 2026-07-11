# cache.py
"""
Domain-aware crawl cache.

Backend: Redis when REDIS_URL is set (or CRAWL_CACHE_BACKEND=redis), else memory.
Supports max_age (ms) reads like AnyCrawl.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

from aiocache import Cache
from aiocache.serializers import JsonSerializer

logger = logging.getLogger(__name__)


def _use_redis() -> bool:
    backend = os.getenv("CRAWL_CACHE_BACKEND", "auto").lower()
    if backend == "memory":
        return False
    if backend == "redis":
        return True
    return bool(os.getenv("REDIS_URL", "").strip())


class CrawlCache:
    def __init__(self):
        self.ttl_map: dict[str, int] = {
            "sec.gov": 86400,
            "earningswhispers": 3600,
            "finance.yahoo": 300,
            "yahoo:full": 1800,
            "yahoo:news": 900,
            "marketwatch": 300,
            "seekingalpha": 1800,
            "wsj.com": 600,
            "reuters.com": 600,
            "default": 600,
        }
        self._init_backend()

    def _init_backend(self) -> None:
        if _use_redis():
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            # aiocache Redis expects endpoint/port; parse simply
            endpoint, port = "localhost", 6379
            try:
                # redis://[:password@]host:port/db
                rest = redis_url.split("://", 1)[-1]
                hostpart = rest.split("@")[-1]
                hostport = hostpart.split("/")[0]
                if ":" in hostport:
                    endpoint, port_s = hostport.rsplit(":", 1)
                    port = int(port_s)
                else:
                    endpoint = hostport
            except Exception:
                pass
            self._cache = Cache(
                Cache.REDIS,
                endpoint=endpoint,
                port=port,
                serializer=JsonSerializer(),
                namespace="crawl",
            )
            self.backend = "redis"
            logger.info("Crawl cache backend=redis (%s:%s)", endpoint, port)
        else:
            self._cache = Cache(
                Cache.MEMORY,
                serializer=JsonSerializer(),
                namespace="crawl",
            )
            self.backend = "memory"
            logger.info("Crawl cache backend=memory")

    def _ttl_for_url(self, url: str) -> int:
        for domain, ttl in self.ttl_map.items():
            if domain in url:
                return ttl
        return self.ttl_map["default"]

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    async def get(self, url: str, max_age_ms: int | None = None) -> Optional[dict]:
        """Return cached payload if present and fresher than max_age_ms (when set)."""
        result = await self._cache.get(self._cache_key(url))
        if result is None:
            logger.debug("CACHE MISS %s", url)
            return None

        cached_at = result.get("_cached_at")
        if max_age_ms is not None and max_age_ms >= 0 and cached_at is not None:
            age_ms = (time.time() - float(cached_at)) * 1000
            if age_ms > max_age_ms:
                logger.debug("CACHE STALE %s age_ms=%.0f max=%s", url, age_ms, max_age_ms)
                return None
            result = dict(result)
            result["maxAge"] = max_age_ms
            result["cachedAt"] = cached_at

        result = dict(result)
        result["cache_hit"] = True
        logger.debug("CACHE HIT  %s", url)
        return result

    async def set(self, url: str, content: dict, ttl: int | None = None):
        ttl = ttl if ttl is not None else self._ttl_for_url(url)
        content_copy = {k: v for k, v in content.items() if k not in ("cache_hit", "cachedAt", "maxAge")}
        content_copy["cache_hit"] = False
        content_copy["_cached_at"] = time.time()
        await self._cache.set(self._cache_key(url), content_copy, ttl=ttl)
        logger.debug("CACHE SET  %s (ttl=%ds backend=%s)", url, ttl, self.backend)

    async def invalidate(self, url: str):
        await self._cache.delete(self._cache_key(url))
        logger.debug("CACHE DEL  %s", url)

    async def clear_all(self):
        await self._cache.clear(namespace="crawl")
        logger.info("Cache cleared (all namespaces).")


cache = CrawlCache()
