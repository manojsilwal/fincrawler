"""Redis-backed Playwright storage_state persistence per retailer + egress."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _redis_key(retailer_key: str, egress_id: str) -> str:
    rk = retailer_key or "unknown"
    eg = egress_id or "direct"
    return f"fincrawler:antibot:cookies:{rk}:{eg}"


def _redis_client():
    import redis

    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)


async def load_storage_state(retailer_key: str, egress_id: str = "direct") -> dict[str, Any] | None:
    from app.config import get_settings

    if not retailer_key or not get_settings().redis_url:
        return None
    try:
        client = _redis_client()
        raw = client.get(_redis_key(retailer_key, egress_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        logger.debug("Failed to load antibot cookies for %s/%s", retailer_key, egress_id, exc_info=True)
        return None


async def save_storage_state(
    retailer_key: str,
    state: dict[str, Any],
    egress_id: str = "direct",
) -> None:
    from app.config import get_settings

    if not retailer_key or not state or not get_settings().redis_url:
        return
    ttl = get_settings().antibot_cookie_ttl_seconds
    try:
        client = _redis_client()
        client.setex(_redis_key(retailer_key, egress_id), ttl, json.dumps(state))
        logger.debug("Saved antibot cookies for %s/%s (ttl=%ss)", retailer_key, egress_id, ttl)
    except Exception:
        logger.debug("Failed to save antibot cookies for %s/%s", retailer_key, egress_id, exc_info=True)


def egress_id_from_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    from urllib.parse import urlparse

    parsed = urlparse(proxy_url)
    host = parsed.hostname or "proxy"
    port = parsed.port or 8080
    return f"{host}:{port}"
