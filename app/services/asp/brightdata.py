"""Backward-compatible re-exports — prefer app.services.asp.providers.brightdata."""

from app.services.asp.providers.brightdata import (
    BrightDataScrapingBrowserProvider,
    BrightDataUnlockerProvider,
    build_native_proxy_url,
    build_scraping_browser_wss,
    fetch_brightdata_unlocker,
    fetch_scraping_browser,
    list_active_zones,
    resolve_zone_name,
)

__all__ = [
    "BrightDataScrapingBrowserProvider",
    "BrightDataUnlockerProvider",
    "build_native_proxy_url",
    "build_scraping_browser_wss",
    "fetch_brightdata_unlocker",
    "fetch_scraping_browser",
    "list_active_zones",
    "resolve_zone_name",
]
