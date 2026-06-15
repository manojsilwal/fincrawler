"""Client for submitting browser-grid scrape jobs and awaiting results."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.services.browser_grid.queue import enqueue_scrape, get_result

logger = logging.getLogger(__name__)


async def fetch_via_browser_grid(url: str, crawled_at: str, retailer_key: str = "") -> dict:
    """Enqueue a stealth-browser scrape and poll Redis for the worker result."""
    settings = get_settings()
    if not settings.enable_browser_grid:
        return {
            "url": url,
            "status": "error",
            "error": "browser_grid_disabled",
            "fetch_backend": "browser_grid",
            "crawled_at": crawled_at,
        }

    try:
        from app.services.asp.proxy_pool import get_next_proxy
        from app.services.asp.proxy_utils import direct_egress_worker_id, is_direct_egress

        proxy_url = None
        preferred_worker_id = None
        if get_settings().browser_proxy_enabled:
            proxy_url = get_next_proxy(retailer_key=retailer_key)
            if proxy_url and is_direct_egress(proxy_url):
                preferred_worker_id = direct_egress_worker_id(proxy_url)
                proxy_url = None

        job_id = await enqueue_scrape(
            url=url,
            retailer_key=retailer_key,
            proxy_url=proxy_url,
            preferred_worker_id=preferred_worker_id,
        )
    except Exception as exc:
        logger.exception("Browser grid enqueue failed")
        return {
            "url": url,
            "status": "error",
            "error": f"browser_grid_enqueue_failed: {exc}",
            "fetch_backend": "browser_grid",
            "crawled_at": crawled_at,
        }

    deadline = time.monotonic() + settings.browser_grid_timeout_seconds
    poll = settings.browser_grid_poll_interval_ms / 1000.0

    while time.monotonic() < deadline:
        result = await get_result(job_id)
        if result is not None:
            result.setdefault("fetch_backend", "browser_grid")
            result.setdefault("browser_grid_job_id", job_id)
            result.setdefault("crawled_at", crawled_at)
            return result
        await asyncio.sleep(poll)

    return {
        "url": url,
        "status": "error",
        "error": "browser_grid_timeout",
        "fetch_backend": "browser_grid",
        "browser_grid_job_id": job_id,
        "crawled_at": crawled_at,
    }
