import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from cache import cache
from crawler import crawl_single

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Firecrawl Compat"])

bearer_scheme = HTTPBearer(auto_error=False)

def verify_firecrawl_auth(
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> None:
    """
    Firecrawl compatibility auth:
    Firecrawl SDK typically sends `Authorization: Bearer <TOKEN>`.
    We also accept `X-Api-Key` to match FinCrawler's native behavior.
    """
    expected_key = os.getenv("API_KEY", "")
    if not expected_key:
        return  # No auth required

    provided_key = None
    if bearer and bearer.credentials:
        provided_key = bearer.credentials
    elif x_api_key:
        provided_key = x_api_key

    if provided_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

class FirecrawlScrapeRequest(BaseModel):
    url: str
    formats: Optional[list[str]] = ["markdown"]
    # We ignore other options like 'onlyMainContent' etc. for now
    # to maintain strict compat with our existing crawl_single logic.

@router.post("/scrape")
async def firecrawl_scrape_endpoint(
    req: FirecrawlScrapeRequest,
    _: None = Depends(verify_firecrawl_auth)
):
    """
    Firecrawl-compatible scrape endpoint.
    Wraps FinCrawler's existing `crawl_single` + `cache` internals.
    """
    url = req.url
    
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    # 1. Cache check
    cached = await cache.get(url)
    if cached:
        result = cached
    else:
        # 2. Live crawl
        result = await crawl_single(url)
        if result.get("status") == "ok":
            await cache.set(url, result)

    if result.get("status") == "error":
        return {
            "success": False,
            "error": result.get("error", "Unknown error")
        }

    # Format result to match Firecrawl's expected ScrapeResponse schema
    return {
        "success": True,
        "data": {
            "markdown": result.get("text", ""),
            "content": result.get("text", ""),
            "metadata": {
                "title": result.get("title", ""),
                "sourceURL": result.get("url", url),
                "statusCode": result.get("http_status", 200)
            }
        }
    }
