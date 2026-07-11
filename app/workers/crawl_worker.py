"""Background crawl worker — consumes Redis crawl-job queue via HybridRouter."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _process_job(job: dict) -> dict:
    from crawler import crawl_single
    from app.services.crawler.product_frontier import run_scoped_pdp_crawl

    job_type = job.get("job_type") or "scrape"
    url = job.get("url") or ""
    options = dict(job.get("options") or {})
    if job.get("source_id"):
        options["source_id"] = job["source_id"]
    if job.get("retailer_key"):
        options["retailer_key"] = job["retailer_key"]

    if job_type == "pdp_crawl":
        return await run_scoped_pdp_crawl(
            seed_url=url,
            max_depth=int(options.get("max_depth", 2)),
            limit=int(options.get("limit", 20)),
            include_paths=options.get("include_paths"),
            exclude_paths=options.get("exclude_paths"),
            retailer_key=options.get("retailer_key") or job.get("retailer_key") or "",
        )

    return await crawl_single(url, options)


async def worker_loop() -> None:
    from app.services.crawl_jobs import queue as crawl_queue

    concurrency = int(os.getenv("CRAWL_WORKER_CONCURRENCY", "2"))
    sem = asyncio.Semaphore(concurrency)
    logger.info("Crawl worker started (concurrency=%s)", concurrency)

    async def _one(job: dict) -> None:
        job_id = job.get("job_id") or str(uuid.uuid4())
        async with sem:
            try:
                result = await _process_job(job)
            except Exception as exc:
                logger.exception("Job %s failed", job_id)
                result = {"status": "error", "error": str(exc), "url": job.get("url")}
            await crawl_queue.store_result(job_id, result)

    pending: set[asyncio.Task] = set()
    while True:
        job = await crawl_queue.dequeue_job(timeout_seconds=5)
        if job:
            task = asyncio.create_task(_one(job))
            pending.add(task)
            task.add_done_callback(pending.discard)
        # reap finished without blocking dequeue forever
        if not job:
            await asyncio.sleep(0.05)


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
