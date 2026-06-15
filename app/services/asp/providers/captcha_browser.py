"""CAPTCHA-aware browser provider — CapSolver / 2Captcha before giving up."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.captcha import get_captcha_solvers
from app.services.asp.providers.base import AspProvider, ScrapeContext


class CaptchaBrowserProvider(AspProvider):
    name = "captcha_browser"

    def is_available(self) -> bool:
        settings = get_settings()
        if not settings.enable_browser_tier4:
            return False
        if settings.enable_antibot_solver:
            return True
        return bool(get_captcha_solvers())

    def should_try(self, ctx: ScrapeContext, profile: dict) -> bool:
        return self.is_available() and self._circuit_ok() and ctx.render_js

    async def fetch(self, ctx: ScrapeContext) -> dict:
        from app.services.asp.provider_health import is_circuit_open
        from app.services.crawler.captcha_fetcher import fetch_with_captcha_solve

        if is_circuit_open(self.name):
            return {
                "url": ctx.url,
                "status": "error",
                "error": "captcha_provider_circuit_open",
                "fetch_backend": self.name,
                "crawled_at": ctx.crawled_at,
            }
        return await fetch_with_captcha_solve(ctx.url, retailer_key=ctx.retailer_key, proxy_url=ctx.proxy)
