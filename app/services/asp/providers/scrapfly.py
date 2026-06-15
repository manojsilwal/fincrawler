"""Optional external Scrapfly provider."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.crawler.managed_backends import fetch_scrapfly


class ScrapflyProvider(AspProvider):
    name = "external_scrapfly"

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.enable_external_scrapfly and bool(settings.scrapfly_api_key.strip())

    async def fetch(self, ctx: ScrapeContext) -> dict:
        return await fetch_scrapfly(ctx.url, ctx.crawled_at)
