"""Redis-backed distributed browser scrape queue."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "fincrawler:browser_grid:jobs"
RESULT_PREFIX = "fincrawler:browser_grid:result:"
RESULT_TTL_SECONDS = 600


def _redis_url() -> str:
    return get_settings().redis_url


async def _client() -> aioredis.Redis:
    return aioredis.from_url(_redis_url(), decode_responses=True)


async def enqueue_scrape(
    *,
    url: str,
    retailer_key: str = "",
    job_id: str | None = None,
    proxy_url: str | None = None,
    preferred_worker_id: str | None = None,
) -> str:
    job_id = job_id or str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "url": url,
        "retailer_key": retailer_key,
        "proxy_url": proxy_url,
        "preferred_worker_id": preferred_worker_id,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    client = await _client()
    try:
        await client.lpush(get_settings().browser_grid_queue_key, json.dumps(payload))
        logger.info("Browser grid job enqueued: %s → %s", job_id, url[:80])
        return job_id
    finally:
        await client.aclose()


async def dequeue_scrape(timeout_seconds: int = 5) -> dict | None:
    client = await _client()
    try:
        item = await client.brpop(get_settings().browser_grid_queue_key, timeout=timeout_seconds)
        if not item:
            return None
        _, raw = item
        return json.loads(raw)
    finally:
        await client.aclose()


async def store_result(job_id: str, result: dict) -> None:
    client = await _client()
    try:
        key = f"{RESULT_PREFIX}{job_id}"
        await client.setex(key, RESULT_TTL_SECONDS, json.dumps(result))
    finally:
        await client.aclose()


async def get_result(job_id: str) -> dict | None:
    client = await _client()
    try:
        raw = await client.get(f"{RESULT_PREFIX}{job_id}")
        if not raw:
            return None
        return json.loads(raw)
    finally:
        await client.aclose()


async def queue_depth() -> int:
    client = await _client()
    try:
        return int(await client.llen(get_settings().browser_grid_queue_key))
    finally:
        await client.aclose()
