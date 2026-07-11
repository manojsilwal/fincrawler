"""Redis-backed async crawl job queue (AnyCrawl-style scrape/crawl jobs)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

RESULT_PREFIX = "fincrawler:crawl_jobs:result:"
STATUS_PREFIX = "fincrawler:crawl_jobs:status:"
DEFAULT_TTL_SECONDS = 3600


def queue_key() -> str:
    return get_settings().crawl_jobs_queue_key


def _redis_url() -> str:
    return get_settings().redis_url


async def _client() -> aioredis.Redis:
    return aioredis.from_url(_redis_url(), decode_responses=True)


async def enqueue_job(
    *,
    url: str,
    job_type: str = "scrape",
    source_id: str | None = None,
    retailer_key: str = "",
    options: dict | None = None,
    job_id: str | None = None,
) -> str:
    job_id = job_id or str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "job_type": job_type,
        "url": url,
        "source_id": source_id,
        "retailer_key": retailer_key,
        "options": options or {},
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    client = await _client()
    try:
        await client.setex(
            f"{STATUS_PREFIX}{job_id}",
            DEFAULT_TTL_SECONDS,
            json.dumps({"status": "queued", "job_id": job_id, "url": url}),
        )
        await client.lpush(queue_key(), json.dumps(payload))
        logger.info("Crawl job enqueued: %s (%s) → %s", job_id, job_type, url[:80])
        return job_id
    finally:
        await client.aclose()


async def dequeue_job(timeout_seconds: int = 5) -> dict | None:
    client = await _client()
    try:
        item = await client.brpop(queue_key(), timeout=timeout_seconds)
        if not item:
            return None
        _, raw = item
        job = json.loads(raw)
        jid = job.get("job_id")
        if jid:
            await client.setex(
                f"{STATUS_PREFIX}{jid}",
                DEFAULT_TTL_SECONDS,
                json.dumps(
                    {
                        "status": "running",
                        "job_id": jid,
                        "url": job.get("url"),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
        return job
    finally:
        await client.aclose()


async def store_result(job_id: str, result: dict, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    client = await _client()
    try:
        status = "completed" if result.get("status") in ("ok", "completed") else "failed"
        if result.get("status") in ("blocked", "rejected", "error"):
            status = "failed"
        envelope = {
            "status": status,
            "job_id": job_id,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        await client.setex(f"{RESULT_PREFIX}{job_id}", ttl, json.dumps(envelope))
        await client.setex(f"{STATUS_PREFIX}{job_id}", ttl, json.dumps(envelope))
    finally:
        await client.aclose()


async def get_job(job_id: str) -> dict | None:
    client = await _client()
    try:
        raw = await client.get(f"{RESULT_PREFIX}{job_id}") or await client.get(
            f"{STATUS_PREFIX}{job_id}"
        )
        if not raw:
            return None
        return json.loads(raw)
    finally:
        await client.aclose()


async def queue_depth() -> int:
    client = await _client()
    try:
        return int(await client.llen(queue_key()))
    finally:
        await client.aclose()
