"""Proxy backend registry — internal egress, Smartproxy, IPRoyal, Oxylabs, static."""

from __future__ import annotations

from app.config import get_settings
from app.services.asp.proxy_backends.base import (
    IPRoyalBackend,
    OxylabsBackend,
    ProxyBackend,
    SmartproxyBackend,
    StaticProxyBackend,
)
from app.services.asp.proxy_backends.internal_egress import InternalEgressBackend


def _parse_static(raw: str) -> list[str]:
    return [u.strip() for u in raw.split(",") if u.strip()]


def get_configured_backends() -> list[ProxyBackend]:
    settings = get_settings()
    backends: list[ProxyBackend] = []

    internal = InternalEgressBackend()
    if internal.is_configured():
        backends.append(internal)

    static = _parse_static(settings.proxy_pool_urls)
    if static:
        backends.append(StaticProxyBackend(static))

    sp = SmartproxyBackend(
        settings.smartproxy_user,
        settings.smartproxy_password,
        settings.smartproxy_host,
        settings.smartproxy_port,
    )
    if sp.is_configured():
        backends.append(sp)

    ir = IPRoyalBackend(
        settings.iproyal_user,
        settings.iproyal_password,
        settings.iproyal_host,
        settings.iproyal_port,
    )
    if ir.is_configured():
        backends.append(ir)

    ox = OxylabsBackend(
        settings.oxylabs_user,
        settings.oxylabs_password,
        settings.oxylabs_host,
        settings.oxylabs_port,
    )
    if ox.is_configured():
        backends.append(ox)

    return backends


def resolve_backends() -> list[ProxyBackend]:
    """Filter backends by PROXY_BACKEND env (auto = all configured)."""
    settings = get_settings()
    all_backends = get_configured_backends()
    mode = (settings.proxy_backend or "auto").strip().lower()

    if mode == "auto":
        return all_backends
    return [b for b in all_backends if b.name == mode]
