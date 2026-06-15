"""curl_cffi TLS impersonation provider."""

from __future__ import annotations

from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.crawler.curl_fetcher import fetch_curl


class CurlImpersonateProvider(AspProvider):
    name = "http_impersonate"

    def should_try(self, ctx: ScrapeContext, profile: dict) -> bool:
        if not ctx.asp or not self._circuit_ok():
            return False
        default_tier = int(profile.get("default_tier", 3))
        return default_tier <= 2

    def is_available(self) -> bool:
        return True

    async def fetch(self, ctx: ScrapeContext) -> dict:
        result = await fetch_curl(ctx.url)
        result["fetch_backend"] = self.name
        result["service"] = "fincrawler-asp"
        return result
