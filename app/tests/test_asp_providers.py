"""Tests for ASP provider registry and proxy pool."""

import os

from app.services.asp.providers.registry import build_provider_chain, list_registered_providers
from app.services.asp.proxy_pool import load_proxy_urls, get_next_proxy, mark_proxy_failure
from app.services.asp.providers.base import ScrapeContext


def test_list_registered_providers():
    names = list_registered_providers()
    assert "js_browser" in names
    assert "captcha_browser" in names
    assert "brightdata_scraping_browser" in names
    assert "proxy_http" in names
    assert "browser_grid" in names


def test_build_provider_chain_includes_local_browser(monkeypatch):
    monkeypatch.setenv("ENABLE_BROWSER_GRID", "false")
    monkeypatch.setenv("ENABLE_BRIGHTDATA_PROVIDER", "false")
    monkeypatch.setenv("BRIGHTDATA_SCRAPING_BROWSER_WSS", "")
    monkeypatch.setenv("BRIGHTDATA_CUSTOMER_ID", "")
    monkeypatch.setenv("ENABLE_BROWSER_TIER4", "true")
    monkeypatch.setenv("ASP_PROVIDER_ORDER", "http_impersonate,js_browser")
    from app.config import get_settings

    get_settings.cache_clear()

    ctx = ScrapeContext(url="https://example.com", crawled_at="2026-01-01T00:00:00Z", retailer_key="bestbuy")
    chain = build_provider_chain(ctx)
    names = [p.name for p in chain]
    assert "js_browser" in names

    get_settings.cache_clear()


def test_proxy_pool_static_urls(monkeypatch):
    monkeypatch.setenv("PROXY_POOL_URLS", "http://user:pass@proxy1:8080,http://user:pass@proxy2:8080")
    monkeypatch.setenv("PROXY_PROVIDER", "static")
    monkeypatch.setenv("MANAGED_PROXY_URL", "")
    monkeypatch.setenv("BRIGHTDATA_CUSTOMER_ID", "")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.asp import proxy_pool as pp

    pp._failures.clear()
    urls = load_proxy_urls()
    assert len(urls) == 2
    p1 = get_next_proxy()
    p2 = get_next_proxy()
    assert p1 != p2 or len(urls) == 1

    get_settings.cache_clear()


def test_proxy_pool_skips_failed(monkeypatch):
    monkeypatch.setenv("PROXY_POOL_URLS", "http://a:1@p1:1,http://b:2@p2:2")
    monkeypatch.setenv("PROXY_MAX_FAILURES", "2")
    monkeypatch.setenv("MANAGED_PROXY_URL", "")
    monkeypatch.setenv("BRIGHTDATA_CUSTOMER_ID", "")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.asp import proxy_pool as pp

    pp._failures.clear()
    urls = load_proxy_urls()
    for _ in range(3):
        mark_proxy_failure(urls[0])
    nxt = get_next_proxy()
    assert nxt == urls[1]

    get_settings.cache_clear()
