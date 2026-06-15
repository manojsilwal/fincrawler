"""ASP scrape API — internal managed scrape service."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.services.asp import ScrapeOptions, asp_engine

router = APIRouter(prefix="/asp", tags=["ASP"])

_API_KEY = os.getenv("API_KEY", "")


class AspScrapeRequest(BaseModel):
    url: str
    asp: bool = True
    render_js: bool = True
    retailer_key: str = ""
    proxy: str | None = None
    retry_on_block: bool = True


def _require_api_key(x_api_key: str = Header(default="")):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-Api-Key")


@router.post("/scrape")
async def asp_scrape(req: AspScrapeRequest, _: None = Depends(_require_api_key)):
    if not req.url.strip():
        raise HTTPException(400, "url is required")
    result = await asp_engine.scrape(
        ScrapeOptions(
            url=req.url.strip(),
            asp=req.asp,
            render_js=req.render_js,
            retailer_key=req.retailer_key.strip(),
            proxy=req.proxy,
            retry_on_block=req.retry_on_block,
        )
    )
    return {
        "service": asp_engine.service_name,
        "result": {k: v for k, v in result.items() if k not in ("html",)},
        "content_chars": len(result.get("html") or result.get("page_text") or ""),
        "status": result.get("status"),
    }


@router.get("/health")
async def asp_health():
    from app.config import get_settings
    from app.services.asp.metrics import get_dashboard
    from app.services.asp.provider_health import health_snapshot, is_budget_exceeded
    from app.services.asp.proxy_pool import pool_status
    from app.services.asp.providers.registry import list_registered_providers

    settings = get_settings()
    grid_depth = None
    egress_nodes = []
    if settings.enable_browser_grid:
        try:
            from app.services.browser_grid.queue import queue_depth

            grid_depth = await queue_depth()
        except Exception:
            grid_depth = -1
    if settings.enable_internal_egress:
        try:
            from app.services.asp.egress_registry import list_egress_nodes

            egress_nodes = await list_egress_nodes()
        except Exception:
            egress_nodes = []

    return {
        "status": "ok",
        "service": asp_engine.service_name,
        "providers": list_registered_providers(),
        "provider_order": settings.asp_provider_order,
        "browser_grid_enabled": settings.enable_browser_grid,
        "browser_grid_queue_depth": grid_depth,
        "internal_egress_enabled": settings.enable_internal_egress,
        "browser_proxy_enabled": settings.browser_proxy_enabled,
        "egress_nodes": egress_nodes,
        "budget_exceeded": is_budget_exceeded(),
        "provider_health": health_snapshot(),
        "proxy_pool": pool_status(),
    }


@router.get("/metrics")
async def asp_metrics(_: None = Depends(_require_api_key)):
    from app.services.asp.metrics import get_dashboard

    return await get_dashboard()
