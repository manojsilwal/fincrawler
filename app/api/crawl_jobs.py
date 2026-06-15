"""Crawl job endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import CrawlJobUrlRequest
from app.services.crawler.hybrid_router import hybrid_router
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
