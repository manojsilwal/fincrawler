"""Bright Data provider — Scraping Browser (CDP) + Web Unlocker API."""

from __future__ import annotations

import logging
import re

import httpx

from app.config import get_settings
from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.crawler.managed_backends import pack_html_result

logger = logging.getLogger(__name__)

_API_BASE = "https://api.brightdata.com"


async def list_active_zones(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{_API_BASE}/zone/get_active_zones",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        return r.json()


async def resolve_zone_name(api_key: str, configured: str) -> str | None:
    if configured.strip():
        return configured.strip()
    try:
        zones = await list_active_zones(api_key)
    except Exception as exc:
        logger.warning("Bright Data zone lookup failed: %s", exc)
        return None
    for z in zones:
        if z.get("type") in ("unblocker", "serp", "browser_api", "res_rotating", "dc"):
            return z.get("name")
    return zones[0]["name"] if zones else None


def build_scraping_browser_wss() -> str | None:
    settings = get_settings()
    if settings.brightdata_scraping_browser_wss.strip():
        return settings.brightdata_scraping_browser_wss.strip()
    customer = settings.brightdata_customer_id.strip()
    zone = settings.brightdata_zone.strip()
    password = settings.brightdata_zone_password.strip()
    if not (customer and zone and password):
        return None
    return f"wss://brd-customer-{customer}-zone-{zone}:{password}@brd.superproxy.io:9222"


def build_native_proxy_url() -> str | None:
    settings = get_settings()
    if settings.managed_proxy_url.strip():
        return settings.managed_proxy_url.strip()
    customer = settings.brightdata_customer_id.strip()
    zone = settings.brightdata_zone.strip()
    password = settings.brightdata_zone_password.strip()
    if not (customer and zone and password):
        return None
    port = settings.brightdata_proxy_port
    user = f"brd-customer-{customer}-zone-{zone}"
    return f"https://{user}:{password}@brd.superproxy.io:{port}"


async def _fetch_unlocker(url: str, crawled_at: str, country: str = "us") -> dict:
    settings = get_settings()
    api_key = settings.brightdata_api_key.strip()
    if not api_key:
        return _error(url, crawled_at, "brightdata_api_key_missing", "brightdata_unlocker")

    zone = await resolve_zone_name(api_key, settings.brightdata_zone)
    if not zone:
        return _error(
            url,
            crawled_at,
            "brightdata_zone_missing_create_web_unlocker_in_dashboard",
            "brightdata_unlocker",
        )

    payload = {"zone": zone, "url": url, "format": "raw", "country": country}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            r = await client.post(
                f"{_API_BASE}/request",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if r.status_code >= 400:
                detail = (r.text or "")[:300]
                return _error(url, crawled_at, f"brightdata_http_{r.status_code}: {detail}", "brightdata_unlocker")

            ctype = (r.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                data = r.json()
                html = data.get("body") or data.get("content") or ""
                status = data.get("status") or r.status_code
                final_url = data.get("url") or url
            else:
                html = r.text
                status = r.status_code
                final_url = url

        return pack_html_result(
            html=html,
            final_url=final_url,
            status=status,
            backend="brightdata_unlocker",
            crawled_at=crawled_at,
        )
    except Exception as exc:
        logger.exception("Bright Data unlocker failed for %s", url)
        return _error(url, crawled_at, str(exc), "brightdata_unlocker")


async def _fetch_scraping_browser(url: str, crawled_at: str, retailer_key: str = "") -> dict:
    from app.services.asp.profiles import get_retailer_profile
    from app.services.crawler.human_behavior import dismiss_consent, human_delay, human_scroll

    cdp_url = build_scraping_browser_wss()
    if not cdp_url:
        return _error(url, crawled_at, "brightdata_scraping_browser_wss_missing", "brightdata_scraping_browser")

    try:
        from playwright.async_api import async_playwright

        html = ""
        final_url = url
        title = ""
        page_text = ""
        http_status = None

        async with async_playwright() as playwright:
            logger.info("Bright Data Scraping Browser → %s", url)
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                profile = get_retailer_profile(retailer_key) if retailer_key else {}
                scroll_wait = int(profile.get("scroll_wait_ms", 5000))
                homepage = profile.get("homepage_url")
                if profile.get("warm_session") and homepage and retailer_key:
                    try:
                        await page.goto(homepage, wait_until="domcontentloaded", timeout=60_000)
                        await human_delay(800, 1500)
                        await dismiss_consent(page, profile.get("consent_selectors", []))
                    except Exception:
                        pass

                resp = await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                http_status = resp.status if resp else None
                await page.wait_for_timeout(3000)
                await dismiss_consent(page, profile.get("consent_selectors", []))

                wait_sel = profile.get("wait_selector")
                if wait_sel:
                    for sel in wait_sel.split(", "):
                        try:
                            await page.wait_for_selector(sel.strip(), timeout=18_000, state="visible")
                            break
                        except Exception:
                            continue

                await human_scroll(page)
                await page.wait_for_timeout(scroll_wait)
                await human_scroll(page)
                await page.wait_for_timeout(2000)

                raw_text = await page.inner_text("body")
                if len(raw_text.strip()) < 2000:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=25_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(5000)
                    await dismiss_consent(page, profile.get("consent_selectors", []))
                    raw_text = await page.inner_text("body")

                html = (await page.content())[:350_000]
                final_url = page.url
                title = await page.title()
                page_text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()[:350_000]
            finally:
                await browser.close()

        if not html:
            raise RuntimeError("empty_scraping_browser_page")

        return pack_html_result(
            html=html,
            final_url=final_url,
            status=http_status,
            backend="brightdata_scraping_browser",
            crawled_at=crawled_at,
            page_text=page_text,
            title=title,
        )
    except Exception as exc:
        logger.exception("Bright Data Scraping Browser failed for %s", url)
        return _error(url, crawled_at, str(exc), "brightdata_scraping_browser")


def _error(url: str, crawled_at: str, error: str, backend: str) -> dict:
    return {
        "url": url,
        "status": "error",
        "error": error,
        "tier_used": 4,
        "tier_name": "bank_grade",
        "fetch_backend": backend,
        "crawled_at": crawled_at,
    }


# Back-compat aliases
fetch_brightdata_unlocker = _fetch_unlocker
fetch_scraping_browser = _fetch_scraping_browser


class BrightDataScrapingBrowserProvider(AspProvider):
    name = "brightdata_scraping_browser"

    def is_available(self) -> bool:
        settings = get_settings()
        if not settings.enable_brightdata_provider:
            return False
        return bool(build_scraping_browser_wss())

    async def fetch(self, ctx: ScrapeContext) -> dict:
        return await _fetch_scraping_browser(ctx.url, ctx.crawled_at, ctx.retailer_key)


class BrightDataUnlockerProvider(AspProvider):
    name = "brightdata_unlocker"

    def is_available(self) -> bool:
        settings = get_settings()
        if not settings.enable_brightdata_provider:
            return False
        zone = settings.brightdata_zone.strip()
        if "scraping_browser" in zone:
            return False
        return bool(settings.brightdata_api_key.strip() and zone)

    async def fetch(self, ctx: ScrapeContext) -> dict:
        settings = get_settings()
        return await _fetch_unlocker(ctx.url, ctx.crawled_at, country=settings.brightdata_country)
