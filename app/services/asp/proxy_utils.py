"""Proxy URL helpers for Playwright and internal egress routing."""

from __future__ import annotations

from urllib.parse import urlparse

DIRECT_EGRESS_PREFIX = "direct://"


def is_direct_egress(proxy_url: str | None) -> bool:
    return bool(proxy_url and proxy_url.startswith(DIRECT_EGRESS_PREFIX))


def direct_egress_worker_id(proxy_url: str) -> str | None:
    if not is_direct_egress(proxy_url):
        return None
    return proxy_url[len(DIRECT_EGRESS_PREFIX) :].strip() or None


def proxy_host_key(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if is_direct_egress(proxy_url):
        return proxy_url
    host = parsed.hostname or "unknown"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{host}:{port}"


def playwright_proxy_kwargs(proxy_url: str | None) -> dict | None:
    """Convert a proxy URL to Playwright ``new_context(proxy=...)`` kwargs."""
    if not proxy_url or is_direct_egress(proxy_url):
        return None

    parsed = urlparse(proxy_url)
    if parsed.scheme not in ("http", "https", "socks5", "socks5h"):
        return None

    scheme = "socks5" if parsed.scheme.startswith("socks5") else parsed.scheme
    if not parsed.hostname:
        return None

    port = parsed.port or (1080 if scheme == "socks5" else 8080)
    server = f"{scheme}://{parsed.hostname}:{port}"
    out: dict = {"server": server}
    if parsed.username:
        out["username"] = parsed.username
    if parsed.password:
        out["password"] = parsed.password
    return out
