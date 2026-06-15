"""Live hybrid POST /shop/search for Zenith."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ShopSearchRequest
from app.services.shop_service import search_product

router = APIRouter(prefix="/shop", tags=["Shopping"])

_API_KEY = os.getenv("API_KEY", "")


def _require_api_key(x_api_key: str = Header(default="")):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-Api-Key")


@router.post("/search")
async def shop_search(req: ShopSearchRequest, db: Session = Depends(get_db), _: None = Depends(_require_api_key)):
    if not req.query.strip():
        raise HTTPException(400, "query is required")
    results = await search_product(
        db,
        query=req.query.strip(),
        retailers=req.retailers,
        max_concurrency=req.max_concurrency,
    )
    ok = sum(1 for r in results if r.get("status") == "ok")
    blocked = sum(1 for r in results if r.get("status") == "blocked")
    return {
        "query": req.query,
        "retailers_attempted": len(results),
        "retailers_success": ok,
        "retailers_blocked": blocked,
        "results": results,
    }


@router.get("/retailers")
def shop_retailers():
    return {
        "retailers": [
            {"key": k, "name": k.title()}
            for k in ("amazon", "walmart", "ebay", "bestbuy", "target")
        ]
    }


@router.post("/google")
async def shop_google_deprecated():
    raise HTTPException(
        status.HTTP_410_GONE,
        detail="Google Shopping direct scrape removed. Use POST /shop/search or GET /rankings/search.",
    )
