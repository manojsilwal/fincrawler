"""Tests for proxy backends, provider health, fingerprints."""

from app.services.asp.provider_health import (
    is_circuit_open,
    record_failure,
    reset_provider,
)
from app.services.asp.proxy_backends import SmartproxyBackend, resolve_backends
from app.services.crawler.fingerprints import pick_fingerprint


def test_smartproxy_build_url():
    b = SmartproxyBackend("myuser", "mypass", "gate.smartproxy.com", 10000)
    urls = b.build_urls(country="us", session_id="amazon123")
    assert len(urls) == 1
    assert "smartproxy.com" in urls[0]
    assert "country-us" in urls[0]


def test_fingerprint_rotation():
    fp1 = pick_fingerprint("amazon")
    fp2 = pick_fingerprint("amazon")
    assert fp1.user_agent  # rotates through profiles
    assert fp2.user_agent


def test_provider_circuit_breaker_on_suspension(monkeypatch):
    import asyncio

    monkeypatch.setenv("PROVIDER_MAX_FAILURES", "3")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_provider("brightdata_scraping_browser")

    async def run():
        await record_failure("brightdata_scraping_browser", error="403 Auth Failed customer_suspended")
        return is_circuit_open("brightdata_scraping_browser")

    assert asyncio.run(run()) is True
    get_settings.cache_clear()


def test_retailer_blocks_do_not_open_circuit(monkeypatch):
    import asyncio

    monkeypatch.setenv("PROVIDER_MAX_FAILURES", "3")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_provider("browser_grid")

    async def run():
        for _ in range(5):
            await record_failure("browser_grid", error="captcha_detected")
        return is_circuit_open("browser_grid")

    assert asyncio.run(run()) is False
    get_settings.cache_clear()


    monkeypatch.setenv("PROXY_POOL_URLS", "http://u:p@proxy.test:8080")
    monkeypatch.setenv("PROXY_BACKEND", "static")
    monkeypatch.setenv("SMARTPROXY_USER", "")
    from app.config import get_settings

    get_settings.cache_clear()
    backends = resolve_backends()
    assert len(backends) == 1
    assert backends[0].name == "static"
    get_settings.cache_clear()
