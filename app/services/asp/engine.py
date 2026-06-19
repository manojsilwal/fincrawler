"""Internal ASP scraping engine — pluggable provider escalation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app.services.asp.detector import is_usable_scrape
from app.services.asp.metrics import record_scrape
from app.services.asp.options import ScrapeOptions
from app.services.asp.provider_health import record_failure, record_success
from app.services.asp.providers.base import ScrapeContext
from app.services.asp.providers.local_browser import LocalBrowserProvider
from app.services.asp.providers.registry import build_provider_chain
from app.services.crawler.session_store import clear_warmed

logger = logging.getLogger(__name__)


class AspEngine:
    """
    FinCrawler's internal Anti-Scraping Protection (ASP) service.

    Escalates through registered providers (see `app/services/asp/providers/`).
    Records metrics, circuit-breaks suspended providers, enforces daily budget.
    """

    service_name = "fincrawler-asp"

    async def scrape(self, options: ScrapeOptions) -> dict:
        return await self.scrape_url(
            options.url,
            asp=options.asp,
            render_js=options.render_js,
            retailer_key=options.retailer_key,
            proxy=options.proxy,
            retry_on_block=options.retry_on_block,
        )

    async def scrape_url(
        self,
        url: str,
        *,
        asp: bool = True,
        render_js: bool = True,
        retailer_key: str = "",
        proxy: str | None = None,
        retry_on_block: bool = True,
    ) -> dict:
        crawled_at = datetime.now(timezone.utc).isoformat()
        ctx = ScrapeContext(
            url=url,
            crawled_at=crawled_at,
            retailer_key=retailer_key,
            proxy=proxy,
            asp=asp,
            render_js=render_js,
        )
        last: dict = {"url": url, "status": "error", "error": "no_attempt", "service": self.service_name}
        attempts: list[str] = []

        chain = build_provider_chain(ctx, proxy_override=proxy)
        if not chain:
            logger.warning("[%s] empty provider chain — using local browser fallback", retailer_key)
            chain = [LocalBrowserProvider()]

        for provider in chain:
            t0 = time.monotonic()
            last = await provider.fetch(ctx)
            latency_ms = (time.monotonic() - t0) * 1000
            attempts.append(provider.name)

            status = last.get("status", "error")
            await record_scrape(
                provider=provider.name,
                retailer_key=retailer_key,
                status=status,
                latency_ms=latency_ms,
                block_reason=last.get("block_reason"),
            )

            if status == "ok":
                if is_usable_scrape(last, retailer_key):
                    await record_success(provider.name, latency_ms=latency_ms)
                    return self._finalize(last, attempts)
                logger.info(
                    "[%s] %s ok but missing product signals — escalating",
                    retailer_key,
                    provider.name,
                )
                continue

            if status == "blocked":
                err = last.get("block_reason") or "blocked"
                await record_failure(provider.name, error=str(err))
                logger.info("[%s] %s blocked (%s)", retailer_key, provider.name, err)
                continue

            err = last.get("error") or last.get("block_reason") or status
            await record_failure(provider.name, error=str(err) if err else None)
            logger.info("[%s] %s (%s)", retailer_key, provider.name, err)

        if retry_on_block and retailer_key and render_js and "browser_grid" not in attempts:
            local = LocalBrowserProvider()
            if local.should_try(ctx, {}):
                clear_warmed(retailer_key)
                t0 = time.monotonic()
                last = await local.fetch(ctx)
                latency_ms = (time.monotonic() - t0) * 1000
                attempts.append("js_browser_retry")
                status = last.get("status", "error")
                await record_scrape(
                    provider="js_browser_retry",
                    retailer_key=retailer_key,
                    status=status,
                    latency_ms=latency_ms,
                )
                if status == "ok" and is_usable_scrape(last, retailer_key):
                    await record_success("js_browser")
                    last["retried_after_block"] = True
                    return self._finalize(last, attempts)
                await record_failure("js_browser", error=last.get("block_reason"))

        from app.services.crawler.vision_fetcher import maybe_apply_vision_fallback

        last = await maybe_apply_vision_fallback(
            last,
            url,
            retailer_key=retailer_key,
            task="shopping" if retailer_key else "finance",
        )
        last["asp_attempts"] = attempts
        last["service"] = self.service_name
        return last

    def _finalize(self, result: dict, attempts: list[str]) -> dict:
        result["asp_attempts"] = attempts
        result["service"] = self.service_name
        if "fetch_backend" not in result:
            result["fetch_backend"] = "asp_engine"
        return result


asp_engine = AspEngine()
