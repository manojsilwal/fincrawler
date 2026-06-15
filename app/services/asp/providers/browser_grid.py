"""Browser grid provider — distributed stealth Playwright workers."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.browser_grid.client import fetch_via_browser_grid


class BrowserGridProvider(AspProvider):
    name = "browser_grid"

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.enable_browser_grid and bool(settings.redis_url)

    def should_try(self, ctx: ScrapeContext, profile: dict) -> bool:
        return self.is_available() and self._circuit_ok() and ctx.render_js

    async def fetch(self, ctx: ScrapeContext) -> dict:
        return await fetch_via_browser_grid(ctx.url, ctx.crawled_at, ctx.retailer_key)
