"""Firecrawl-compatible /v1/scrape — HybridRouter + multi-format output."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from cache import cache
from crawler import crawl_single

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Firecrawl Compat"])
bearer_scheme = HTTPBearer(auto_error=False)


def verify_firecrawl_auth(
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    expected_key = os.getenv("API_KEY", "")
    if not expected_key:
        return
    provided_key = None
    if bearer and bearer.credentials:
        provided_key = bearer.credentials
    elif x_api_key:
        provided_key = x_api_key
    if provided_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


class JsonOptions(BaseModel):
    schema_: Optional[dict[str, Any]] = Field(default=None, alias="schema")
    user_prompt: Optional[str] = None
    schema_name: Optional[str] = None
    schema_description: Optional[str] = None

    model_config = {"populate_by_name": True}


class FirecrawlScrapeRequest(BaseModel):
    url: str
    formats: Optional[list[str]] = ["markdown"]
    onlyMainContent: Optional[bool] = True
    only_main_content: Optional[bool] = None
    includeTags: Optional[list[str]] = None
    excludeTags: Optional[list[str]] = None
    include_tags: Optional[list[str]] = None
    exclude_tags: Optional[list[str]] = None
    max_age: Optional[int] = None  # ms; 0 = skip cache read
    store_in_cache: Optional[bool] = True
    json_options: Optional[JsonOptions] = None
    retailer_key: Optional[str] = None


@router.post("/scrape")
async def firecrawl_scrape_endpoint(
    req: FirecrawlScrapeRequest,
    _: None = Depends(verify_firecrawl_auth),
):
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    formats = req.formats or ["markdown"]
    only_main = (
        req.only_main_content
        if req.only_main_content is not None
        else (req.onlyMainContent if req.onlyMainContent is not None else True)
    )
    include_tags = req.include_tags or req.includeTags
    exclude_tags = req.exclude_tags or req.excludeTags

    max_age = req.max_age
    if max_age is None:
        max_age = int(os.getenv("CRAWL_CACHE_DEFAULT_MAX_AGE_MS", "600000"))

    cached = None
    if max_age != 0:
        cached = await cache.get(url, max_age_ms=max_age)

    if cached:
        result = cached
    else:
        options: dict[str, Any] = {}
        if req.retailer_key:
            options["retailer_key"] = req.retailer_key
        result = await crawl_single(url, options)
        if result.get("status") == "ok" and req.store_in_cache is not False:
            await cache.set(url, result)

    if result.get("status") in ("error", "blocked", "rejected"):
        return {
            "success": False,
            "error": result.get("error")
            or result.get("block_reason")
            or result.get("reason")
            or "scrape_failed",
            "data": {
                "url": result.get("url", url),
                "status": result.get("status"),
            },
        }

    from app.services.crawler.html_transformer import transform_page

    html = result.get("html") or ""
    formatted = transform_page(
        html,
        base_url=result.get("url") or url,
        formats=formats,
        only_main_content=bool(only_main),
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    # Prefer live text if markdown missing
    if "markdown" in formats and not formatted.get("markdown"):
        formatted["markdown"] = result.get("text") or result.get("page_text") or ""
        formatted["content"] = formatted["markdown"]

    data: dict[str, Any] = {
        "url": result.get("url", url),
        "status": "completed",
        "metadata": {
            "title": result.get("title", ""),
            "sourceURL": result.get("url", url),
            "statusCode": result.get("http_status", 200),
            "tier_used": result.get("tier_used"),
            "tier_name": result.get("tier_name"),
        },
        **formatted,
    }
    if result.get("cache_hit"):
        data["cachedAt"] = result.get("cachedAt") or result.get("_cached_at")
        data["maxAge"] = result.get("maxAge", max_age)

    if req.json_options and "json" in formats:
        data["json"] = await _extract_json(result, req.json_options)

    return {"success": True, "data": data}


async def _extract_json(result: dict, options: JsonOptions) -> Any:
    try:
        from llm import extract_structured

        prompt = options.user_prompt or "Extract the key fields from this page as JSON."
        text = result.get("text") or result.get("page_text") or ""
        if result.get("html") and len(text) < 200:
            from app.services.crawler.html_transformer import html_to_markdown

            text = html_to_markdown(result["html"])

        if options.schema_ and isinstance(options.schema_, dict):
            prompt = (
                f"{prompt}\n\nRespond with JSON matching this schema:\n"
                f"{options.schema_}"
            )

        return await extract_structured(
            text[:120_000],
            prompt,
            schema=None,
            extra_context=options.schema_description,
            task="shopping",
        )
    except Exception as exc:
        logger.exception("json_options extraction failed")
        return {"_error": str(exc)}
