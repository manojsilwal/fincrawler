"""Enhanced proxy pool — pluggable backends, sticky sessions, health scoring."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

from app.config import get_settings
from app.services.asp.proxy_utils import proxy_host_key

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_index = 0
_failures: dict[str, int] = {}
_successes: dict[str, int] = {}
_sticky: dict[str, str] = {}  # retailer_key -> proxy_url
_sticky_expiry: dict[str, float] = {}


def _parse_proxy_urls(raw: str) -> list[str]:
    urls: list[str] = []
    for part in raw.split(","):
        u = part.strip()
        if not u:
            continue
        if u.startswith("direct://"):
            urls.append(u)
            continue
        parsed = urlparse(u)
        if parsed.scheme in ("http", "https", "socks5", "socks5h", "direct"):
            urls.append(u)
    return urls


def _brightdata_proxy_url() -> str | None:
    from app.services.asp.providers.brightdata import build_native_proxy_url

    url = build_native_proxy_url()
    if url and "scraping_browser" not in url:
        return url
    return None


def _redis_client():
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def _redis_enabled() -> bool:
    return get_settings().proxy_pool_redis and bool(get_settings().redis_url)


def _redis_get_int(key: str) -> int:
    try:
        val = _redis_client().get(key)
        return int(val) if val else 0
    except Exception:
        return 0


def _redis_incr(key: str, ttl: int = 86400) -> None:
    try:
        client = _redis_client()
        client.incr(key)
        client.expire(key, ttl)
    except Exception:
        pass


def _redis_delete_prefix_for_proxy(proxy_url: str) -> None:
    try:
        client = _redis_client()
        host_key = proxy_host_key(proxy_url)
        for key in client.scan_iter(match=f"fincrawler:proxy:*:{host_key}"):
            client.delete(key)
    except Exception:
        pass


def load_proxy_urls(*, retailer_key: str = "", country: str = "us") -> list[str]:
    """Load proxy endpoints from pluggable backends + static URLs."""
    settings = get_settings()
    urls = _parse_proxy_urls(settings.proxy_pool_urls)

    from app.services.asp.proxy_backends import resolve_backends

    session_id = retailer_key or None
    for backend in resolve_backends():
        for u in backend.build_urls(country=country, session_id=session_id):
            if u not in urls:
                urls.append(u)

    provider = (settings.proxy_provider or "auto").strip().lower()
    if provider in ("auto", "brightdata", "mixed"):
        bd = _brightdata_proxy_url()
        if bd and bd not in urls:
            urls.append(bd)

    if not urls and settings.managed_proxy_url.strip():
        mp = settings.managed_proxy_url.strip()
        if "scraping_browser" not in mp:
            urls = [mp]

    return urls


def _health_score(proxy_url: str, retailer_key: str = "") -> float:
    host_key = proxy_host_key(proxy_url)
    if _redis_enabled():
        ok = _redis_get_int(f"fincrawler:proxy:ok:{host_key}")
        fail = _redis_get_int(f"fincrawler:proxy:fail:{host_key}")
        if retailer_key:
            rok = _redis_get_int(f"fincrawler:proxy:rok:{retailer_key}:{host_key}")
            rfail = _redis_get_int(f"fincrawler:proxy:rfail:{retailer_key}:{host_key}")
            ok += rok
            fail += rfail
    else:
        ok = _successes.get(proxy_url, 0)
        fail = _failures.get(proxy_url, 0)
    total = ok + fail
    if total == 0:
        return 0.5
    return ok / total


def mark_proxy_failure(proxy_url: str, *, retailer_key: str = "") -> None:
    host_key = proxy_host_key(proxy_url)
    with _lock:
        _failures[proxy_url] = _failures.get(proxy_url, 0) + 1
        for rk, url in list(_sticky.items()):
            if url == proxy_url:
                _sticky.pop(rk, None)
                _sticky_expiry.pop(rk, None)
    if _redis_enabled():
        _redis_incr(f"fincrawler:proxy:fail:{host_key}")
        if retailer_key:
            _redis_incr(f"fincrawler:proxy:rfail:{retailer_key}:{host_key}")
        try:
            client = _redis_client()
            for rk in list(_sticky.keys()):
                sticky_key = f"fincrawler:proxy:sticky:{rk}"
                if client.get(sticky_key) == proxy_url:
                    client.delete(sticky_key)
        except Exception:
            pass


def mark_proxy_success(proxy_url: str, *, retailer_key: str = "") -> None:
    host_key = proxy_host_key(proxy_url)
    with _lock:
        _successes[proxy_url] = _successes.get(proxy_url, 0) + 1
        _failures.pop(proxy_url, None)
    if _redis_enabled():
        _redis_incr(f"fincrawler:proxy:ok:{host_key}")
        if retailer_key:
            _redis_incr(f"fincrawler:proxy:rok:{retailer_key}:{host_key}")
        _redis_delete_prefix_for_proxy(proxy_url)


def get_sticky_proxy(retailer_key: str) -> str | None:
    if not retailer_key:
        return None
    settings = get_settings()
    if not settings.proxy_sticky_sessions:
        return None
    if _redis_enabled():
        try:
            val = _redis_client().get(f"fincrawler:proxy:sticky:{retailer_key}")
            if val:
                return val
        except Exception:
            pass
    with _lock:
        exp = _sticky_expiry.get(retailer_key, 0)
        if time.time() < exp:
            return _sticky.get(retailer_key)
    return None


def set_sticky_proxy(retailer_key: str, proxy_url: str) -> None:
    if not retailer_key:
        return
    settings = get_settings()
    ttl = settings.proxy_sticky_ttl_seconds
    with _lock:
        _sticky[retailer_key] = proxy_url
        _sticky_expiry[retailer_key] = time.time() + ttl
    if _redis_enabled():
        try:
            client = _redis_client()
            client.setex(f"fincrawler:proxy:sticky:{retailer_key}", ttl, proxy_url)
        except Exception:
            pass


def clear_sticky_proxy(retailer_key: str) -> None:
    with _lock:
        _sticky.pop(retailer_key, None)
        _sticky_expiry.pop(retailer_key, None)
    if _redis_enabled():
        try:
            _redis_client().delete(f"fincrawler:proxy:sticky:{retailer_key}")
        except Exception:
            pass


def get_next_proxy(*, retailer_key: str = "", skip_failed: bool = True, country: str = "us") -> str | None:
    """Select proxy: sticky session > health-scored round-robin."""
    sticky = get_sticky_proxy(retailer_key)
    if sticky:
        return sticky

    urls = load_proxy_urls(retailer_key=retailer_key, country=country)
    if not urls:
        return None

    settings = get_settings()
    max_failures = settings.proxy_max_failures

    with _lock:
        global _index
        candidates = []
        for i, url in enumerate(urls):
            fail_count = _failures.get(url, 0)
            if _redis_enabled():
                fail_count = max(fail_count, _redis_get_int(f"fincrawler:proxy:fail:{proxy_host_key(url)}"))
            if skip_failed and fail_count >= max_failures:
                continue
            candidates.append((-_health_score(url, retailer_key), i, url))
        if not candidates:
            candidates = [(0, i, u) for i, u in enumerate(urls)]

        candidates.sort()
        pick = candidates[_index % len(candidates)][2]
        _index += 1

    if retailer_key and settings.proxy_sticky_sessions:
        set_sticky_proxy(retailer_key, pick)
    return pick


def pool_status() -> dict:
    urls = load_proxy_urls()
    with _lock:
        status = {
            "count": len(urls),
            "provider": get_settings().proxy_backend,
            "sticky_sessions": len(_sticky),
            "redis_backed": _redis_enabled(),
            "internal_egress": get_settings().enable_internal_egress,
            "failures": dict(_failures),
            "successes": dict(_successes),
            "health_scores": {u: round(_health_score(u), 2) for u in urls[:20]},
            "urls_redacted": [urlparse(u).hostname or u.replace("direct://", "worker:") for u in urls],
        }
    if _redis_enabled():
        try:
            from app.services.asp.egress_registry import list_egress_nodes_sync

            status["egress_nodes"] = len(list_egress_nodes_sync())
        except Exception:
            status["egress_nodes"] = 0
    return status
