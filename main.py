# main.py
"""
FinCrawler — FastAPI web service entry point.

Endpoints
---------
GET  /health        Liveness + LLM connectivity probe
GET  /quote         Yahoo Finance quote (regex fast-path, then LLM fallback)
GET  /quote/smart   LLM-powered structured quote extraction via DeepSeek v4 Pro
POST /scrape        Cache-first URL scrape → raw text (API-key protected)
POST /extract       Crawl + chunk + LLM extract → structured JSON (API-key protected)
DELETE /cache       Clear all cached entries (API-key protected)
"""

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl

from browser_pool import pool
from cache import cache
from crawler import crawl_single
from extractor import extract_from_page, extract_quote
from prefetch import start_scheduler
from shop_crawler import search_product, RETAILERS

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


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    llm_online: bool


class QuoteResponse(BaseModel):
    ok: bool
    ticker: str
    price: Optional[float] = None
    currency: str = "USD"
    source: str = "yahoo"
    error: Optional[str] = None


class SmartQuoteData(BaseModel):
    regularMarketPrice: Optional[float] = None
    regularMarketChangePercent: Optional[float] = None
    regularMarketVolume: Optional[int] = None
    fiftyTwoWeekHigh: Optional[float] = None
    fiftyTwoWeekLow: Optional[float] = None
    trailingPE: Optional[float] = None
    marketCap: Optional[float] = None
    shortName: Optional[str] = None


class SmartQuoteResponse(BaseModel):
    ok: bool
    ticker: str
    data: Optional[SmartQuoteData] = None
    cache_hit: bool = False
    chunks_used: int = 0
    total_chunks: int = 0
    error: Optional[str] = None



class ScrapeRequest(BaseModel):
    url: str
    force_refresh: bool = False
    max_bytes: Optional[int] = None
    # Tiered crawl envelope (see CONTRACT.md)
    tier: Optional[int] = None
    tier_name: Optional[str] = None
    max_tier: int = 4
    auto_escalate: bool = True
    session_id: Optional[str] = None
    warm_session: Optional[bool] = None
    retailer_key: Optional[str] = None
    fingerprint_profile: str = "chrome_mac_us"
    behavior: Optional[dict[str, Any]] = None
    proxy: Optional[dict[str, Any]] = None

    def crawl_options(self) -> dict[str, Any]:
        return self.model_dump(exclude={"url", "force_refresh"}, exclude_none=True)


class ScrapeResponse(BaseModel):
    url: str
    title: Optional[str] = None
    text: Optional[str] = None
    char_count: Optional[int] = None
    http_status: Optional[int] = None
    status: str
    cache_hit: bool = False
    crawled_at: Optional[str] = None
    error: Optional[str] = None
    tier_used: Optional[int] = None
    tier_name: Optional[str] = None
    detection_hits: Optional[list[str]] = None
    block_reason: Optional[str] = None
    session_id: Optional[str] = None


class ExtractRequest(BaseModel):
    url: str = Field(..., description="Target URL to scrape and extract from")
    prompt: str = Field(
        ...,
        description=(
            "Natural language instruction, e.g. "
            "'Extract current stock price, P/E ratio, and 52-week range'"
        ),
    )
    extra_context: Optional[str] = Field(
        None,
        description="Optional extra context injected into the LLM system prompt (e.g. ticker symbol)",
    )
    force_refresh: bool = Field(False, description="Bypass cache and re-crawl + re-extract")


class ExtractResponse(BaseModel):
    url: str
    prompt: str
    data: dict[str, Any]
    cache_hit: bool
    chunks_used: int
    total_chunks: int
    status: str
    validation_error: Optional[str] = None
    error: Optional[str] = None


class ShopSearchRequest(BaseModel):
    query: str = Field(..., description="Product name to search, e.g. 'DJI Osmo Pocket 3'")
    retailers: Optional[list[str]] = Field(
        None,
        description="Subset of: amazon, walmart, ebay, bestbuy, target. Omit for all.",
    )
    max_concurrency: int = Field(3, ge=1, le=5, description="Parallel browser contexts (1-5)")
    google_fallback: bool = Field(
        True,
        description=(
            "Run Google Shopping in parallel and use it to fill in prices "
            "for any retailer that blocks the direct crawl."
        ),
    )
    tier: Optional[int] = None
    tier_name: Optional[str] = None
    max_tier: int = 4
    auto_escalate: bool = True
    session_id: Optional[str] = None
    warm_session: Optional[bool] = None
    retailer_key: Optional[str] = None
    fingerprint_profile: str = "chrome_mac_us"
    behavior: Optional[dict[str, Any]] = None
    proxy: Optional[dict[str, Any]] = None

    def crawl_options(self) -> dict[str, Any]:
        return self.model_dump(
            exclude={"query", "retailers", "max_concurrency", "google_fallback"},
            exclude_none=True,
        )


class GoogleShopRequest(BaseModel):
    tier: Optional[int] = None
    tier_name: Optional[str] = None
    max_tier: int = 4
    auto_escalate: bool = True
    session_id: Optional[str] = None
    warm_session: Optional[bool] = None
    retailer_key: str = "google_shopping"
    fingerprint_profile: str = "chrome_mac_us"
    behavior: Optional[dict[str, Any]] = None
    proxy: Optional[dict[str, Any]] = None

    def crawl_options(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ShopResultData(BaseModel):
    product_name: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    currency: str = "USD"
    availability: Optional[str] = None
    seller: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    product_url: Optional[str] = None
    savings: Optional[float] = None


class ShopRetailerResult(BaseModel):
    retailer: str
    retailer_key: str
    query: str
    url: str
    status: str
    data: Optional[dict[str, Any]] = None
    llm_extraction: bool = False
    char_count: Optional[int] = None
    http_status: Optional[int] = None
    block_reason: Optional[str] = None
    error: Optional[str] = None
    crawled_at: Optional[str] = None
    tier_used: Optional[int] = None
    tier_name: Optional[str] = None
    detection_hits: Optional[list[str]] = None
    session_id: Optional[str] = None


class ShopSearchResponse(BaseModel):
    query: str
    retailers_attempted: int
    retailers_success: int
    retailers_blocked: int
    results: list[dict[str, Any]]


class CardRecommendationRequest(BaseModel):
    category: str = Field(..., description="Category for card recommendation, e.g. 'dining', 'travel', 'groceries'")

class CardRecommendationResponse(BaseModel):
    status: str
    category: str
    url: Optional[str] = None
    cards: list[dict[str, Any]]
    total_cards: int
    crawled_at: Optional[str] = None
    error: Optional[str] = None

class PointsUsageRequest(BaseModel):
    points_program: str = Field(..., description="Points program, e.g. 'Chase Ultimate Rewards', 'Amex Membership Rewards'")
    spend_category: str = Field(..., description="Category to spend on, e.g. 'flights to Europe', 'luxury hotels'")

class PointsUsageResponse(BaseModel):
    status: str
    points_program: str
    spend_category: str
    url: Optional[str] = None
    strategies: list[dict[str, Any]]
    total_strategies: int
    crawled_at: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Legacy regex-based Yahoo price extraction (kept as fast-path fallback)
# ---------------------------------------------------------------------------

def _parse_yahoo_regular_price(page_text: str) -> Optional[float]:
    """Best-effort extract last trade from Yahoo quote embedded JSON."""
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
    Load Yahoo quote URL; parse embedded JSON and/or fin-streamer DOM nodes.
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
    description=(
        "Async financial web-crawler microservice with LLM-powered extraction "
        "via DeepSeek v4 Pro (NVIDIA API). Serves the TradeTalk Finance Agent."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to finance-agent Render URL in prod
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes — Firecrawl compat
# ---------------------------------------------------------------------------
from firecrawl_compat import router as firecrawl_router
app.include_router(firecrawl_router)


# ---------------------------------------------------------------------------
# Routes — Infra
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Infra"], response_model=HealthResponse)
async def health():
    """
    Liveness probe — no auth required.
    Also checks LLM endpoint reachability (non-blocking: returns llm_online=false on timeout).
    """
    llm_ok = False
    try:
        from llm import llm_health_check
        llm_ok = await llm_health_check()
    except Exception:
        pass
    return HealthResponse(status="ok", service="fincrawler", llm_online=llm_ok)


@app.delete("/cache", tags=["Infra"])
async def clear_cache(x_api_key: str = Header(default="")):
    """Evict all cached entries (raw scrapes + LLM extractions)."""
    _require_api_key(x_api_key)
    await cache.clear_all()
    return {"status": "cache cleared"}


# ---------------------------------------------------------------------------
# Routes — Crawler (raw)
# ---------------------------------------------------------------------------

@app.get("/quote", tags=["Crawler"], response_model=QuoteResponse)
async def quote_yahoo(
    ticker: str,
    _: None = Depends(_verify_bearer_or_x_api_key),
):
    """
    Regex fast-path: scrape Yahoo Finance quote HTML and parse the embedded
    ``regularMarketPrice`` JSON field.  No LLM call — use ``/quote/smart``
    for richer structured data.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")

    url = f"https://finance.yahoo.com/quote/{sym}"
    price, err = await _fetch_yahoo_quote_price(url)
    if err:
        raise HTTPException(status_code=502, detail=err)
    if price is None:
        return QuoteResponse(ok=False, ticker=sym, error="price_not_found")
    return QuoteResponse(ok=True, ticker=sym, price=round(price, 4))


@app.get("/quote/smart", tags=["Crawler"], response_model=SmartQuoteResponse)
async def quote_smart(
    ticker: str,
    force_refresh: bool = False,
    _: None = Depends(_verify_bearer_or_x_api_key),
):
    """
    **LLM-powered quote extraction** via DeepSeek v4 Pro.

    Crawls Yahoo Finance, chunks the page, retrieves the most relevant
    sections, and instructs the LLM to extract a full structured quote
    (price, change %, volume, 52-week range, P/E, market cap, company name).

    Results are cached by (url + prompt) for 5 minutes.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")

    result = await extract_quote(ticker=sym, force_refresh=force_refresh)

    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result.get("error", "extraction_failed"))

    raw_data = result.get("data", {})
    # Try to coerce to typed model; fall back gracefully
    try:
        quote_data = SmartQuoteData.model_validate(raw_data)
    except Exception:
        quote_data = None

    return SmartQuoteResponse(
        ok=True,
        ticker=sym,
        data=quote_data,
        cache_hit=result.get("cache_hit", False),
        chunks_used=result.get("chunks_used", 0),
        total_chunks=result.get("total_chunks", 0),
    )


@app.post("/scrape", tags=["Crawler"], response_model=ScrapeResponse)
@app.post("/crawl", tags=["Crawler"], response_model=ScrapeResponse)
async def scrape(
    req: Optional[ScrapeRequest] = None,
    url: Optional[str] = None,
    force_refresh: bool = False,
    x_api_key: str = Header(default=""),
):
    _require_api_key(x_api_key)
    
    # Resolve parameters from body or query
    target_url = url
    refresh = force_refresh
    crawl_opts: dict[str, Any] | None = None
    if req:
        target_url = req.url or target_url
        refresh = req.force_refresh or refresh
        crawl_opts = req.crawl_options()
        
    if not target_url:
        raise HTTPException(status_code=400, detail="url is required.")

    # L1: cache check
    if not refresh:
        cached = await cache.get(target_url)
        if cached:
            return JSONResponse(content=cached)

    # L2: tiered crawl
    result = await crawl_single(target_url, crawl_options=crawl_opts)

    if result["status"] == "ok":
        await cache.set(target_url, result)

    # Zenith worker expects title/excerpt/status_code shape for /crawl path
    if result.get("html") and not result.get("excerpt"):
        result["excerpt"] = (result.get("text") or "")[:400]
    if result.get("http_status") is not None and "status_code" not in result:
        result["status_code"] = result["http_status"]

    return JSONResponse(
        content=result,
        status_code=200 if result["status"] == "ok" else 502,
    )


# ---------------------------------------------------------------------------
# Routes — LLM Extraction (new)
# ---------------------------------------------------------------------------

@app.post("/extract", tags=["LLM Extraction"], response_model=ExtractResponse)
async def extract(
    req: ExtractRequest,
    x_api_key: str = Header(default=""),
):
    """
    **Intelligent extraction endpoint** — the flagship feature of FinCrawler v2.

    Pipeline:
    1. `crawl_single(url)` — Playwright browser fetch
    2. `chunk_text(text)` — token-aware paragraph splitting (≈3K tokens / chunk)
    3. `select_top_chunks(chunks, prompt)` — keyword-relevance retrieval (top 4)
    4. `extract_structured(context, prompt)` — DeepSeek v4 Pro → JSON
    5. Return typed, cached result

    **Use cases:**
    - `"Extract current price, P/E ratio, and 52-week range"` → stock page
    - `"List all earnings dates and EPS estimates"` → earnings calendar
    - `"Extract the company's revenue, net income, and EPS for the last 4 quarters"` → SEC filing
    - `"Summarise the key risk factors"` → 10-K

    Results cached by `hash(url + prompt)` with domain-aware TTL.
    """
    _require_api_key(x_api_key)

    if not req.url:
        raise HTTPException(status_code=400, detail="url is required")
    if not req.prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    result = await extract_from_page(
        url=req.url,
        prompt=req.prompt,
        force_refresh=req.force_refresh,
        extra_context=req.extra_context,
    )

    http_status = 200
    if result.get("status") == "error":
        http_status = 502

    return JSONResponse(content=result, status_code=http_status)


# ---------------------------------------------------------------------------
# Routes — Shopping (new)
# ---------------------------------------------------------------------------

@app.post("/shop/search", tags=["Shopping"], response_model=ShopSearchResponse)
async def shop_search(
    req: ShopSearchRequest,
    x_api_key: str = Header(default=""),
):
    """
    **Multi-retailer product price comparison** with stealth crawling + LLM extraction.

    Fans out to up to 5 major retailers in parallel using:
    - Anti-bot stealth browser (7 JS patches, randomised UA/viewport)
    - Human-like scrolling & timing delays
    - Cloudflare/challenge page detection & wait
    - Cookie consent auto-dismiss
    - DeepSeek v4 Pro LLM → structured product data

    Example request body:
    ```json
    {
      "query": "DJI Osmo Pocket 3",
      "retailers": ["amazon", "walmart", "ebay", "bestbuy", "target"]
    }
    ```

    Supported retailer keys: **amazon, walmart, ebay, bestbuy, target**

    Each result contains:
    - `status`: ok | blocked | error
    - `data.product_name`, `data.price`, `data.original_price`, `data.discount_pct`
    - `data.availability`, `data.rating`, `data.review_count`, `data.product_url`
    - `block_reason` if the retailer blocked the request
    """
    _require_api_key(x_api_key)

    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    # Validate retailer keys
    valid_keys = set(RETAILERS.keys())
    unknown = [r for r in (req.retailers or []) if r not in valid_keys]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown retailer(s): {unknown}. Valid: {sorted(valid_keys)}",
        )

    results = await search_product(
        query=req.query.strip(),
        retailers=req.retailers,
        max_concurrency=req.max_concurrency,
        google_fallback=req.google_fallback,
        crawl_options=req.crawl_options(),
    )

    ok_count      = sum(1 for r in results if r.get("status") in ("ok", "ok_via_google"))
    blocked_count = sum(1 for r in results if r.get("status") == "blocked")

    return ShopSearchResponse(
        query=req.query,
        retailers_attempted=len(results),
        retailers_success=ok_count,
        retailers_blocked=blocked_count,
        results=results,
    )


@app.post("/shop/google", tags=["Shopping"])
async def shop_google(
    query: str,
    body: Optional[GoogleShopRequest] = None,
    x_api_key: str = Header(default=""),
):
    """
    **Direct Google Shopping search** — single request, data from all retailers.

    Crawls `https://www.google.com/search?q={query}&tbm=shop` with full stealth,
    then uses DeepSeek v4 Pro to extract every product listing on the page.

    Returns a unified list of price offers across Amazon, Walmart, eBay,
    Best Buy, Target and any other retailers Google shows — in one call.

    Much faster than `/shop/search` when you only need prices and don't need
    to navigate individual product pages.
    """
    _require_api_key(x_api_key)
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    from google_shop import google_shop_search
    crawl_opts = body.crawl_options() if body else None
    result = await google_shop_search(query=query.strip(), crawl_options=crawl_opts)

    status_code = 200 if result.get("status") == "ok" else 502
    return JSONResponse(content=result, status_code=status_code)


@app.get("/shop/retailers", tags=["Shopping"])
async def shop_retailers():
    """List all supported retailer keys and their names."""
    return {
        "retailers": [
            {"key": k, "name": v["name"], "search_url_template": v["search_url"]}
            for k, v in RETAILERS.items()
        ]
    }


# ---------------------------------------------------------------------------
# Routes — Cards & Points (new)
# ---------------------------------------------------------------------------

@app.post("/cards/recommend", tags=["Cards"], response_model=CardRecommendationResponse)
async def cards_recommend(
    req: CardRecommendationRequest,
    x_api_key: str = Header(default=""),
):
    """
    Search and recommend best credit cards for a specific category.
    """
    _require_api_key(x_api_key)
    if not req.category or not req.category.strip():
        raise HTTPException(status_code=400, detail="category is required")

    from cards_crawler import search_card_recommendations
    result = await search_card_recommendations(req.category.strip())
    
    status_code = 200 if result.get("status") == "ok" else 502
    return JSONResponse(content=result, status_code=status_code)

@app.post("/cards/points-usage", tags=["Cards"], response_model=PointsUsageResponse)
async def points_usage(
    req: PointsUsageRequest,
    x_api_key: str = Header(default=""),
):
    """
    Search and find the best usage strategies for a points program on a specific spend category.
    """
    _require_api_key(x_api_key)
    if not req.points_program or not req.points_program.strip():
        raise HTTPException(status_code=400, detail="points_program is required")
    if not req.spend_category or not req.spend_category.strip():
        raise HTTPException(status_code=400, detail="spend_category is required")

    from cards_crawler import search_points_usage
    result = await search_points_usage(req.points_program.strip(), req.spend_category.strip())

    status_code = 200 if result.get("status") == "ok" else 502
    return JSONResponse(content=result, status_code=status_code)
