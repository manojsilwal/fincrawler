"""Crawl job endpoints — sync URL fetch + async Redis jobs + scoped PDP crawl."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import CrawlJobEnqueueRequest, CrawlJobUrlRequest, ScopedPdpCrawlRequest
from app.services.crawler.hybrid_router import hybrid_router
from app.services.crawler.product_frontier import run_scoped_pdp_crawl
from app.services.source_registry import SourceRegistry

router = APIRouter(prefix="/crawl-jobs", tags=["CrawlJobs"])
_registry = SourceRegistry()


@router.post("/url")
async def crawl_url(body: CrawlJobUrlRequest, db: Session = Depends(get_db)):
    source = _registry.get(db, body.source_id)
    if not source:
        raise HTTPException(404, "source not found")
    result = await hybrid_router.fetch(db, source, body.url)
    return result


@router.post("/enqueue")
async def enqueue_crawl_job(body: CrawlJobEnqueueRequest):
    from app.services.crawl_jobs import queue as crawl_queue

    options = {
        "max_depth": body.max_depth,
        "limit": body.limit,
        "include_paths": body.include_paths,
        "exclude_paths": body.exclude_paths,
        "strategy": body.strategy,
    }
    if body.retailer_key:
        options["retailer_key"] = body.retailer_key

    job_id = await crawl_queue.enqueue_job(
        url=body.url,
        job_type=body.job_type,
        source_id=str(body.source_id) if body.source_id else None,
        retailer_key=body.retailer_key or "",
        options=options,
    )
    depth = await crawl_queue.queue_depth()
    return {"job_id": job_id, "status": "queued", "queue_depth": depth}


@router.get("/jobs/{job_id}")
async def get_crawl_job(job_id: str):
    from app.services.crawl_jobs import queue as crawl_queue

    job = await crawl_queue.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/queue")
async def crawl_queue_status():
    from app.services.crawl_jobs import queue as crawl_queue

    return {"queue_depth": await crawl_queue.queue_depth()}


@router.post("/pdp-crawl")
async def scoped_pdp_crawl(body: ScopedPdpCrawlRequest):
    """Shopping-scoped product-page discovery (same-domain, utility-scored)."""
    if body.async_job:
        from app.services.crawl_jobs import queue as crawl_queue

        job_id = await crawl_queue.enqueue_job(
            url=body.url,
            job_type="pdp_crawl",
            retailer_key=body.retailer_key or "",
            options={
                "max_depth": body.max_depth,
                "limit": body.limit,
                "include_paths": body.include_paths,
                "exclude_paths": body.exclude_paths,
                "strategy": body.strategy,
                "retailer_key": body.retailer_key,
            },
        )
        return {"job_id": job_id, "status": "queued"}

    return await run_scoped_pdp_crawl(
        seed_url=body.url,
        max_depth=body.max_depth,
        limit=body.limit,
        strategy=body.strategy,
        include_paths=body.include_paths,
        exclude_paths=body.exclude_paths,
        retailer_key=body.retailer_key or "",
    )


@router.get("/events")
def list_events(db: Session = Depends(get_db), limit: int = 100):
    from app.models import CrawlEvent

    rows = db.query(CrawlEvent).order_by(CrawlEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "source_id": str(r.source_id) if r.source_id else None,
            "url": r.url,
            "event_type": r.event_type,
            "http_status": r.http_status,
            "message": r.message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
