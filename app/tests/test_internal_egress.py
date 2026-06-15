"""Tests for internal egress and proxy utilities."""

from app.services.asp.proxy_backends.internal_egress import InternalEgressBackend, _with_sticky_session
from app.services.asp.proxy_utils import (
    direct_egress_worker_id,
    is_direct_egress,
    playwright_proxy_kwargs,
    proxy_host_key,
)


def test_playwright_proxy_kwargs():
    out = playwright_proxy_kwargs("http://user:secret@proxy.example.com:3128")
    assert out == {
        "server": "http://proxy.example.com:3128",
        "username": "user",
        "password": "secret",
    }


def test_direct_egress_pseudo_urls():
    assert is_direct_egress("direct://worker-abc")
    assert direct_egress_worker_id("direct://worker-abc") == "worker-abc"
    assert playwright_proxy_kwargs("direct://worker-abc") is None
    assert proxy_host_key("direct://worker-abc") == "direct://worker-abc"


def test_sticky_session_username():
    base = "http://egress:pass@10.0.0.5:3128"
    sticky = _with_sticky_session(base, "ebay", "us")
    assert "session-ebay" in sticky
    assert "cc-us" in sticky


def test_internal_egress_backend_static(monkeypatch):
    monkeypatch.setenv("ENABLE_INTERNAL_EGRESS", "true")
    monkeypatch.setenv("INTERNAL_EGRESS_ENDPOINTS", "http://a:b@p1:3128")
    monkeypatch.setenv("INTERNAL_EGRESS_USE_WORKER_SLOTS", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    backend = InternalEgressBackend()
    assert backend.is_configured()
    urls = backend.build_urls(country="us", session_id="target")
    assert len(urls) == 1
    assert "p1" in urls[0]
    get_settings.cache_clear()
