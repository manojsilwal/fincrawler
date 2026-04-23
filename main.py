# main.py
"""
FinCrawler — FastAPI web service entry point.

Endpoints
---------
GET  /health        Liveness probe (Render health check)
GET  /quote         Yahoo Finance quote scrape → spot price (API-key protected when configured)
POST /scrape        Cache-first URL scrape (API-key protected)
DELETE /cache       Clear all cached entries (API-key protected)
"""

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
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


def _verify_bearer_or_x_api_key(
    x_api_key: str = Header(default=""),
    authorization: Optional[str] = Header(default=None),
):
    """Accept X-Api-Key or Authorization: Bearer (matches TradeTalk FinCrawlerClient)."""
    if not _API_KEY:
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key
    if token != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


def _parse_yahoo_regular_price(page_text: str) -> Optional[float]:
    """Best-effort extract last trade from Yahoo quote HTML / embedded JSON (incl. script tags)."""
    if not page_text:
        return None
    patterns = (
        r'"regularMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"regularMarketPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"currentPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"postMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"preMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    )
    for pat in patterns:
        m = re.search(pat, page_text)
        if m:
            try:
                v = float(m.group(1))
                return v if v > 0 else None
            except ValueError:
                continue
    return None


async def _fetch_yahoo_quote_price(url: str) -> tuple[Optional[float], Optional[str]]:
    """
    Load Yahoo quote URL; parse embedded JSON in HTML and/or visible streamer nodes.
    Returns (price, error_message).
    """
    from browser_pool import pool

    try:
        async with pool.acquire() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            html = await page.content()
            price = _parse_yahoo_regular_price(html)
            if price is not None:
                return price, None
            # Yahoo often renders price in fin-streamer (client-side); not always in static HTML.
            for sel in (
                "[data-field='regularMarketPrice']",
                "fin-streamer[data-field='regularMarketPrice']",
            ):
                try:
                    loc = page.locator(sel)
                    if await loc.count() < 1:
                        continue
                    first = loc.first
                    raw = await first.get_attribute("value")
                    if raw:
                        price = float(raw)
                        if price > 0:
                            return price, None
                    txt = (await first.inner_text()).strip().replace(",", "")
                    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", txt)
                    if m:
                        price = float(m.group(1))
                        if price > 0:
                            return price, None
                except Exception:
                    continue
            return None, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


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

from firecrawl_compat import router as firecrawl_router
app.include_router(firecrawl_router)

@app.get("/health", tags=["Infra"])
async def health():
    """Liveness probe — no auth required."""
    return {"status": "ok", "service": "fincrawler"}


@app.get("/quote", tags=["Crawler"])
async def quote_yahoo(
    ticker: str,
    _: None = Depends(_verify_bearer_or_x_api_key),
):
    """
    Return regular-market spot for a US ticker by scraping Yahoo quote HTML.
    Used when upstream yfinance from another datacenter returns empty history.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")

    url = f"https://finance.yahoo.com/quote/{sym}"
    price, err = await _fetch_yahoo_quote_price(url)
    if err:
        raise HTTPException(status_code=502, detail=err)
    if price is None:
        return JSONResponse(
            content={
                "ok": False,
                "ticker": sym,
                "error": "price_not_found",
            },
            status_code=422,
        )
    return {"ok": True, "ticker": sym, "price": round(price, 4), "currency": "USD"}


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
