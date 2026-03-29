# main.py
"""
FinCrawler — FastAPI web service entry point.

Endpoints
---------
GET  /health        Liveness probe (Render health check)
POST /scrape        Cache-first URL scrape (API-key protected)
DELETE /cache       Clear all cached entries (API-key protected)
"""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from browser_pool import pool
from cache import cache
from crawler import crawl_single
from prefetch import start_scheduler

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
_API_KEY = os.getenv("API_KEY", "")

def _require_api_key(x_api_key: str = Header(default="")):
    """Dependency: reject requests whose X-Api-Key header doesn't match."""
    if not _API_KEY:
        # No key configured → open access (useful for local dev, not recommended for prod)
        return
    if x_api_key != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key header.",
        )


# ---------------------------------------------------------------------------
# Lifespan: start / stop browser pool + scheduler
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting FinCrawler service…")
    await pool.initialize()
    scheduler = start_scheduler()
    yield
    logger.info("Shutting down FinCrawler service…")
    scheduler.shutdown(wait=False)
    await pool.teardown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="FinCrawler",
    description="Async financial web-crawler microservice for the Finance Agent.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your finance-agent Render URL in prod
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Infra"])
async def health():
    """Liveness probe — no auth required."""
    return {"status": "ok", "service": "fincrawler"}


@app.post("/scrape", tags=["Crawler"])
async def scrape(
    url: str,
    force_refresh: bool = False,
    x_api_key: str = Header(default=""),
):
    """
    Scrape *url* and return its text content.

    - Returns cached result if available (unless force_refresh=true).
    - Caches successful results with domain-aware TTL.
    - Requires **X-Api-Key** header in production.
    """
    _require_api_key(x_api_key)

    if not url:
        raise HTTPException(status_code=400, detail="url query parameter is required.")

    # L1: cache check
    if not force_refresh:
        cached = await cache.get(url)
        if cached:
            return JSONResponse(content=cached)

    # L2: live crawl
    result = await crawl_single(url)

    if result["status"] == "ok":
        await cache.set(url, result)

    return JSONResponse(
        content=result,
        status_code=200 if result["status"] == "ok" else 502,
    )


@app.delete("/cache", tags=["Infra"])
async def clear_cache(x_api_key: str = Header(default="")):
    """
    Evict all cached entries.
    Useful during development or to force a full re-crawl of S&P 500 data.
    """
    _require_api_key(x_api_key)
    await cache.clear_all()
    return {"status": "cache cleared"}
