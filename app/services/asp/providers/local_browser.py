"""Local stealth Playwright provider."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.providers.base import AspProvider, ScrapeContext


class LocalBrowserProvider(AspProvider):
    name = "js_browser"

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.enable_browser_tier4

    def should_try(self, ctx: ScrapeContext, profile: dict) -> bool:
        return self.is_available() and self._circuit_ok() and ctx.render_js

    async def fetch(self, ctx: ScrapeContext) -> dict:
        from app.services.crawler.browser_fetcher import fetch_stealth_browser

        return await fetch_stealth_browser(ctx.url, retailer_key=ctx.retailer_key)
