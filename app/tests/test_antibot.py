"""Tests for in-house antibot solver and grid price extraction."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.asp.antibot.detector import detect_antibot_vendor
from app.services.asp.antibot.cookie_store import egress_id_from_proxy


def test_detect_perimeterx():
    html = '<div id="px-captcha">Press & Hold</div><script src="https://captcha.px-cdn.net"></script>'
    assert detect_antibot_vendor(html=html, url="https://www.walmart.com/blocked") == "perimeterx"


def test_detect_datadome():
    html = '<iframe src="https://geo.captcha-delivery.com/captcha/"></iframe>'
    assert detect_antibot_vendor(html=html) == "datadome"


def test_detect_recaptcha():
    html = '<div class="g-recaptcha" data-sitekey="abc123"></div>'
    assert detect_antibot_vendor(html=html) == "recaptcha"


def test_egress_id_from_proxy():
    assert egress_id_from_proxy(None) == "direct"
    assert egress_id_from_proxy("http://user:pass@10.0.0.5:3128") == "10.0.0.5:3128"


def test_shop_merge_pre_candidates():
    """Grid worker price_candidates_usd should merge into extraction."""
    from shop_price_extract import merge_shop_extraction

    merged = merge_shop_extraction(
        {"product_name": "DJI Osmo Pocket 3", "price": None},
        [419.0, 539.0],
        query="dji osmo pocket 3",
        retailer_key="target",
    )
    assert merged.get("price") == 419.0
    assert merged.get("price_source") == "regex"


def test_grid_worker_strips_html_keeps_candidates():
    html = (
        '<div data-test="current-price">$34.99</div>'
        '<script>{"current_retail": 419.00}</script>'
    )
    from shop_price_extract import extract_prices_from_html, price_rich_excerpt

    candidates = extract_prices_from_html(html, "target")
    assert any(p >= 34 for p in candidates)
    excerpt = price_rich_excerpt("", html, max_len=5000)
    assert excerpt


@pytest.mark.asyncio
async def test_cookie_store_roundtrip(monkeypatch):
    store: dict[str, str] = {}

    class FakeRedis:
        def get(self, key):
            return store.get(key)

        def setex(self, key, ttl, value):
            store[key] = value

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ANTIBOT_COOKIE_TTL_SECONDS", "3600")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.services.asp.antibot import cookie_store

    monkeypatch.setattr(cookie_store, "_redis_client", lambda: FakeRedis())

    state = {"cookies": [{"name": "_px3", "value": "abc", "domain": ".walmart.com"}]}
    await cookie_store.save_storage_state("walmart", state, "direct")
    loaded = await cookie_store.load_storage_state("walmart", "direct")
    assert loaded == state
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_perimeterx_press_and_hold_invoked():
    from app.services.asp.antibot.perimeterx import solve_perimeterx

    page = AsyncMock()
    page.url = "https://www.walmart.com/search?q=test"
    page.content = AsyncMock(return_value="<html>ok products</html>")
    page.title = AsyncMock(return_value="Walmart Search")
    page.inner_text = AsyncMock(return_value="DJI Osmo Pocket 3 $419")
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    frame = AsyncMock()
    frame.locator.return_value.count = AsyncMock(return_value=1)
    frame.locator.return_value.first.bounding_box = AsyncMock(
        return_value={"x": 100, "y": 200, "width": 180, "height": 50}
    )
    page.locator.return_value.count = AsyncMock(return_value=1)
    page.locator.return_value.first.content_frame = AsyncMock(return_value=frame)

    with patch("app.services.asp.antibot.perimeterx._is_still_blocked", AsyncMock(side_effect=[True, False])):
        with patch("app.services.asp.antibot.perimeterx._press_and_hold", AsyncMock(return_value=True)) as hold:
            ok = await solve_perimeterx(page, max_attempts=2)
    assert ok is True
    hold.assert_called()
