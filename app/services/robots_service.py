"""robots.txt enforcement with caching."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)


class RobotsService:
    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser | None] = {}

    async def _load_parser(self, base_url: str) -> RobotFileParser | None:
        if base_url in self._cache:
            return self._cache[base_url]
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(robots_url)
                if r.status_code >= 400:
                    self._cache[base_url] = None
                    return None
                parser.parse(r.text.splitlines())
        except Exception as exc:
            logger.warning("robots unavailable for %s: %s", robots_url, exc)
            self._cache[base_url] = None
            return None
        self._cache[base_url] = parser
        return parser

    async def can_fetch(self, url: str, user_agent: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = await self._load_parser(base)
        if parser is None:
            return False, "robots_unavailable"
        if parser.can_fetch(user_agent, url):
            return True, "allowed_by_robots"
        return False, "disallowed_by_robots"
