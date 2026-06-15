"""Browser grid worker — consumes Redis scrape jobs and runs stealth Playwright."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_running = True


def _handle_signal(*_):
    global _running
    _running = False
    logger.info("Browser grid worker shutting down…")


async def _heartbeat_egress(worker_id: str) -> None:
    from app.services.asp.egress_registry import fetch_public_ip, register_egress

    region = os.getenv("GCP_REGION", os.getenv("ZONE", ""))
    proxy_url = os.getenv("WORKER_PROXY_URL", "").strip() or None
    while _running:
        try:
            ip = await fetch_public_ip()
            await register_egress(
                worker_id=worker_id,
                egress_ip=ip,
                proxy_url=proxy_url,
                region=region,
            )
        except Exception:
            logger.debug("Egress heartbeat failed", exc_info=True)
        await asyncio.sleep(45)


async def _process_job(job: dict) -> dict:
    from app.services.crawler.browser_fetcher import fetch_stealth_browser

    url = job["url"]
    retailer_key = job.get("retailer_key") or ""
    proxy_url = job.get("proxy_url")
    logger.info("Processing browser grid job %s → %s", job.get("job_id"), url[:100])
    result = await fetch_stealth_browser(
        url,
        retailer_key=retailer_key,
        proxy_url=proxy_url,
        fetch_backend="browser_grid",
    )
    result["browser_grid_job_id"] = job.get("job_id")
    result["fetch_backend"] = "browser_grid"
    result["worker_id"] = os.getenv("BROWSER_GRID_WORKER_ID", os.getenv("HOSTNAME", "local"))
    result["processed_at"] = datetime.now(timezone.utc).isoformat()
    return {k: v for k, v in result.items() if k not in ("html",)}


async def run_worker() -> None:
    from app.services.browser_grid.queue import dequeue_scrape, store_result
    import redis.asyncio as aioredis
    from app.config import get_settings

    worker_id = os.getenv("BROWSER_GRID_WORKER_ID", os.getenv("HOSTNAME", "local"))
    logger.info("Browser grid worker started (id=%s)", worker_id)
    asyncio.create_task(_heartbeat_egress(worker_id))

    settings = get_settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    queue_key = settings.browser_grid_queue_key

    try:
        while _running:
            try:
                item = await client.brpop(queue_key, timeout=5)
            except TimeoutError:
                continue
            except Exception as exc:
                if "Timeout reading" in str(exc):
                    continue
                logger.warning("Redis BRPOP error: %s", exc)
                await asyncio.sleep(1)
                continue
            if not item:
                continue
            _, raw = item
            job = json.loads(raw)
            preferred = job.get("preferred_worker_id")
            if preferred and preferred != worker_id:
                await client.lpush(queue_key, raw)
                await asyncio.sleep(0.3)
                continue

            job_id = job.get("job_id", "unknown")
            try:
                result = await _process_job(job)
                key = f"fincrawler:browser_grid:result:{job_id}"
                await client.setex(key, 600, json.dumps(result))
                logger.info(
                    "Job %s done: status=%s chars=%s",
                    job_id,
                    result.get("status"),
                    result.get("char_count"),
                )
            except Exception:
                logger.exception("Job %s failed", job_id)
                await client.setex(
                    f"fincrawler:browser_grid:result:{job_id}",
                    600,
                    json.dumps(
                        {
                            "url": job.get("url"),
                            "status": "error",
                            "error": "browser_grid_worker_exception",
                            "fetch_backend": "browser_grid",
                            "browser_grid_job_id": job_id,
                        }
                    ),
                )
    finally:
        await client.aclose()


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
