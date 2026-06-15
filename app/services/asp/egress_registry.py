"""Redis-backed internal egress node registry (self-hosted proxy fleet)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_EGRESS_INDEX = "fincrawler:egress:index"
_EGRESS_NODE_PREFIX = "fincrawler:egress:node:"
_EGRESS_TTL_SECONDS = 120


def _node_key(worker_id: str) -> str:
    return f"{_EGRESS_NODE_PREFIX}{worker_id}"


def _redis_sync():
    import redis

    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)


async def _redis_async():
    import redis.asyncio as aioredis

    from app.config import get_settings

    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


async def register_egress(
    *,
    worker_id: str,
    egress_ip: str,
    proxy_url: str | None = None,
    region: str = "",
) -> None:
    """Heartbeat an egress node (browser-grid worker or Squid VM)."""
    payload = {
        "worker_id": worker_id,
        "egress_ip": egress_ip,
        "proxy_url": proxy_url or "",
        "region": region,
        "last_seen": time.time(),
    }
    client = await _redis_async()
    try:
        key = _node_key(worker_id)
        await client.setex(key, _EGRESS_TTL_SECONDS, json.dumps(payload))
        await client.sadd(_EGRESS_INDEX, worker_id)
    finally:
        await client.aclose()


async def fetch_public_ip() -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            resp.raise_for_status()
            return resp.json().get("ip", "unknown")
    except Exception:
        logger.warning("Could not resolve public egress IP")
        return "unknown"


def list_egress_nodes_sync() -> list[dict[str, Any]]:
    try:
        client = _redis_sync()
        worker_ids = client.smembers(_EGRESS_INDEX)
        nodes: list[dict[str, Any]] = []
        now = time.time()
        for wid in worker_ids:
            raw = client.get(_node_key(wid))
            if not raw:
                continue
            node = json.loads(raw)
            if now - float(node.get("last_seen", 0)) > _EGRESS_TTL_SECONDS:
                continue
            nodes.append(node)
        return nodes
    except Exception:
        logger.debug("Egress registry unavailable", exc_info=True)
        return []


async def list_egress_nodes() -> list[dict[str, Any]]:
    client = None
    try:
        client = await _redis_async()
        worker_ids = await client.smembers(_EGRESS_INDEX)
        nodes: list[dict[str, Any]] = []
        now = time.time()
        for wid in worker_ids:
            raw = await client.get(_node_key(wid))
            if not raw:
                continue
            node = json.loads(raw)
            if now - float(node.get("last_seen", 0)) > _EGRESS_TTL_SECONDS:
                continue
            nodes.append(node)
        return nodes
    except Exception:
        logger.debug("Egress registry unavailable", exc_info=True)
        return []
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


def build_direct_egress_urls(*, retailer_key: str = "") -> list[str]:
    """Pseudo proxy URLs that pin browser-grid jobs to a specific worker egress."""
    nodes = list_egress_nodes_sync()
    if not nodes:
        return []
    urls = [f"direct://{n['worker_id']}" for n in nodes if n.get("worker_id")]
    if retailer_key and urls:
        # Stable retailer → worker affinity without sticky HTTP proxy
        idx = hash(retailer_key) % len(urls)
        return [urls[idx]] + [u for i, u in enumerate(urls) if i != idx]
    return urls
