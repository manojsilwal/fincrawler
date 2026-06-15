"""Robots service tests."""

import pytest

from app.services.robots_service import RobotsService


@pytest.mark.asyncio
async def test_walmart_search_disallowed_for_bot():
    svc = RobotsService()
    can, reason = await svc.can_fetch(
        "https://www.walmart.com/search?q=test",
        "ShoppingIntelBot/1.0",
    )
    assert not can
    assert reason == "disallowed_by_robots"


@pytest.mark.asyncio
async def test_robots_cache_reused():
    svc = RobotsService()
    url = "https://www.walmart.com/ip/123"
    await svc.can_fetch(url, "ShoppingIntelBot/1.0")
    assert "https://www.walmart.com" in svc._cache


@pytest.mark.asyncio
async def test_unavailable_robots_returns_false(monkeypatch):
    svc = RobotsService()

    async def fail_get(*args, **kwargs):
        raise OSError("network down")

    import httpx

    class BadClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url):
            raise OSError("down")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: BadClient())
    svc._cache.clear()
    can, reason = await svc.can_fetch("https://unknown-invalid.example.com/page", "Bot/1.0")
    assert not can
    assert reason == "robots_unavailable"
