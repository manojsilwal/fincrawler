"""Zenith-compatible /crawl and /scrape aliases."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.crawler.hybrid_router import hybrid_router
from app.services.source_registry import SourceRegistry

router = APIRouter(tags=["ZenithCompat"])

_API_KEY = os.getenv("API_KEY", "")
_registry = SourceRegistry()


def _auth(x_api_key: str = Header(default=""), authorization: str | None = Header(default=None)):
    if not _API_KEY:
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key
    if token != _API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")


class CrawlRequest(BaseModel):
    url: str
    retailer_key: str | None = None
    max_bytes: int | None = Field(default=350_000)


@router.post("/crawl")
@router.post("/scrape")
async def crawl_compat(body: CrawlRequest, db: Session = Depends(get_db), _: None = Depends(_auth)):
    source = None
    if body.retailer_key:
        source = _registry.get_by_retailer(db, body.retailer_key)
    if not source:
        from app.models import Source

        source = db.query(Source).filter(Source.status == "active").first()
    if not source:
        raise HTTPException(400, "no active source configured")

    result = await hybrid_router.fetch(db, source, body.url)
    from app.services.asp.detector import is_usable_scrape
    from app.services.crawler.vision_fetcher import maybe_apply_vision_fallback, vision_fallback_enabled

    retailer_key = body.retailer_key or (source.retailer_key if source else "")
    if vision_fallback_enabled():
        needs_vision = result.get("status") != "ok" or not is_usable_scrape(result, retailer_key)
        if needs_vision:
            result = await maybe_apply_vision_fallback(
                result,
                body.url,
                retailer_key=retailer_key,
                task="shopping" if retailer_key else "finance",
            )

    html = result.get("html") or result.get("text") or ""
    payload = {
        "url": result.get("url", body.url),
        "status_code": result.get("http_status"),
        "title": result.get("title"),
        "excerpt": (result.get("text") or "")[:2000],
        "html": html[: body.max_bytes or 350_000] if result.get("status") == "ok" else None,
        "status": result.get("status"),
        "tier_used": result.get("tier_used"),
        "tier_name": result.get("tier_name"),
        "block_reason": result.get("block_reason"),
        "detection_hits": result.get("detection_hits", []),
        "escalated_from": result.get("escalated_from"),
    }
    if result.get("vision_data"):
        payload["vision_data"] = result["vision_data"]
        payload["vision_fallback"] = True
        payload["vision_source"] = result.get("vision_source")
    return payload
