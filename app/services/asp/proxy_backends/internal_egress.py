"""Internal self-hosted egress backend — mimics residential session routing."""

from __future__ import annotations

from urllib.parse import quote, urlparse

from app.config import get_settings
from app.services.asp.egress_registry import build_direct_egress_urls, list_egress_nodes_sync
from app.services.asp.proxy_backends.base import ProxyBackend


def _with_sticky_session(proxy_url: str, session_id: str | None, country: str) -> str:
    """Embed sticky session + country in proxy username (Bright Data-style)."""
    if not session_id:
        return proxy_url
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return proxy_url
    user = parsed.username or "egress"
    session = session_id[:16]
    sticky_user = f"{user}-session-{session}-cc-{country}"
    password = parsed.password or ""
    host = parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{quote(sticky_user, safe='')}:{quote(password, safe='')}@{host}{port}"


class InternalEgressBackend(ProxyBackend):
    """FinCrawler-owned egress pool — Squid fleet + browser-grid worker proxies."""

    name = "internal"

    def is_configured(self) -> bool:
        settings = get_settings()
        if not settings.enable_internal_egress:
            return False
        if settings.internal_egress_endpoints.strip():
            return True
        return bool(list_egress_nodes_sync() or build_direct_egress_urls())

    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        settings = get_settings()
        urls: list[str] = []

        for part in settings.internal_egress_endpoints.split(","):
            base = part.strip()
            if base:
                urls.append(_with_sticky_session(base, session_id, country))

        for node in list_egress_nodes_sync():
            proxy_url = (node.get("proxy_url") or "").strip()
            if proxy_url and proxy_url not in urls:
                urls.append(_with_sticky_session(proxy_url, session_id, country))

        if settings.internal_egress_use_worker_slots:
            for direct in build_direct_egress_urls(retailer_key=session_id or ""):
                if direct not in urls:
                    urls.append(direct)

        return urls
