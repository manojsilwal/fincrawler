"""Proxy-backed httpx provider using the internal proxy pool."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.proxy_pool import get_next_proxy, mark_proxy_failure, mark_proxy_success, load_proxy_urls
from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.crawler.managed_backends import fetch_proxy_http


class ProxyHttpProvider(AspProvider):
    name = "proxy_http"

    def __init__(self, proxy_override: str | None = None) -> None:
        self._proxy_override = proxy_override

    def is_available(self) -> bool:
        if self._proxy_override:
            return "scraping_browser" not in self._proxy_override
        return bool(load_proxy_urls())

    async def fetch(self, ctx: ScrapeContext) -> dict:
        proxy = self._proxy_override or get_next_proxy(retailer_key=ctx.retailer_key)
        if not proxy or "scraping_browser" in proxy:
            return {
                "url": ctx.url,
                "status": "error",
                "error": "proxy_unavailable",
                "fetch_backend": self.name,
                "crawled_at": ctx.crawled_at,
            }

        result = await fetch_proxy_http(ctx.url, ctx.crawled_at, proxy=proxy)
        result["fetch_backend"] = self.name
        result["proxy_used"] = proxy.split("@")[-1] if "@" in proxy else "configured"

        if result.get("status") == "ok":
            mark_proxy_success(proxy)
        elif result.get("status") in ("error", "blocked"):
            mark_proxy_failure(proxy)

        return result
