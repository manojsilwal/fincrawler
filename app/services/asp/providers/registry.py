"""ASP provider registry — ordered escalation chain."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.profiles import get_retailer_profile
from app.services.asp.providers.base import AspProvider, ScrapeContext
from app.services.asp.providers.brightdata import (
    BrightDataScrapingBrowserProvider,
    BrightDataUnlockerProvider,
)
from app.services.asp.providers.browser_grid import BrowserGridProvider
from app.services.asp.providers.captcha_browser import CaptchaBrowserProvider
from app.services.asp.providers.curl_impersonate import CurlImpersonateProvider
from app.services.asp.providers.local_browser import LocalBrowserProvider
from app.services.asp.providers.proxy_http import ProxyHttpProvider
from app.services.asp.providers.scrapfly import ScrapflyProvider

# Phase 1 internal first → captcha → proxy → paid external last
_DEFAULT_ORDER = (
    "browser_grid",
    "http_impersonate",
    "js_browser",
    "captcha_browser",
    "proxy_http",
    "brightdata_scraping_browser",
    "brightdata_unlocker",
    "external_scrapfly",
)

_PROVIDER_CLASSES: dict[str, type[AspProvider]] = {
    "browser_grid": BrowserGridProvider,
    "brightdata_scraping_browser": BrightDataScrapingBrowserProvider,
    "http_impersonate": CurlImpersonateProvider,
    "js_browser": LocalBrowserProvider,
    "captcha_browser": CaptchaBrowserProvider,
    "brightdata_unlocker": BrightDataUnlockerProvider,
    "proxy_http": ProxyHttpProvider,
    "external_scrapfly": ScrapflyProvider,
}


def _parse_order(raw: str) -> list[str]:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return names or list(_DEFAULT_ORDER)


def build_provider_chain(
    ctx: ScrapeContext,
    *,
    proxy_override: str | None = None,
    include_local_browser: bool = True,
) -> list[AspProvider]:
    """Build the ordered list of providers to try for this scrape."""
    settings = get_settings()
    profile = get_retailer_profile(ctx.retailer_key) if ctx.retailer_key else {}
    order = _parse_order(settings.asp_provider_order)

    providers: list[AspProvider] = []
    for name in order:
        cls = _PROVIDER_CLASSES.get(name)
        if not cls:
            continue
        if name == "proxy_http":
            provider = cls(proxy_override=proxy_override or ctx.proxy)
        else:
            provider = cls()
        if not provider.is_available():
            continue
        if not provider.should_try(ctx, profile):
            continue
        if name == "js_browser" and not include_local_browser:
            continue
        providers.append(provider)

    return providers


def list_registered_providers() -> list[str]:
    return list(_PROVIDER_CLASSES.keys())
