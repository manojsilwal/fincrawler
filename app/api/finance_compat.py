"""TradeTalk finance endpoints — quote, news, SEC, legacy scrape, Firecrawl /v1/scrape."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["FinanceCompat"])

_API_KEY = os.getenv("API_KEY", "")


def _auth(
    x_api_key: str = Header(default=""),
    authorization: str | None = Header(default=None),
):
    if not _API_KEY:
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key
    if token != _API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")


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


class FullQuoteResponse(BaseModel):
    ok: bool
    ticker: str
    url: str = ""
    source: str = ""
    cache_hit: bool = False
    field_count: int = 0
    modules_fetched: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class FinanceScrapeRequest(BaseModel):
    url: str
    force_refresh: bool = False
    include_html: bool = False


class ScrapeRequest(BaseModel):
    url: str
    force_refresh: bool = False


def _parse_yahoo_regular_price(page_text: str, symbol: str | None = None) -> Optional[float]:
    from app.services.yahoo_finance import parse_yahoo_regular_price

    return parse_yahoo_regular_price(page_text, symbol)


async def _fetch_page_text(url: str) -> tuple[str, Optional[str]]:
    """Tier-1 HTTP fetch, then legacy Playwright crawl as fallback."""
    from app.services.crawler.compliant_fetcher import fetch_compliant

    result = await fetch_compliant(url)
    if result.get("status") == "ok":
        html = result.get("html") or result.get("text") or ""
        if html:
            return html, None

    try:
        from crawler import crawl_single

        crawled = await crawl_single(url)
        if crawled.get("status") == "ok":
            return crawled.get("html") or crawled.get("text") or "", None
        return "", crawled.get("error") or "crawl_failed"
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)


async def _fetch_yahoo_quote_price(url: str, symbol: str | None = None) -> tuple[Optional[float], Optional[str]]:
    from app.services.yahoo_finance import fetch_yahoo_full, ticker_from_yahoo_url

    sym = symbol or ticker_from_yahoo_url(url)
    if sym:
        full = await fetch_yahoo_full(sym)
        data = full.get("data") or {}
        for key in (
            "price.regularMarketPrice",
            "asp.regularMarketPrice",
            "dom.regularMarketPrice",
            "vision.regularMarketPrice",
            "regularMarketPrice",
            "html.regularMarketPrice",
        ):
            val = data.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return float(val), None

    html, err = await _fetch_page_text(url)
    if err and not html:
        return None, err
    price = _parse_yahoo_regular_price(html, sym)
    if price is not None:
        return price, None
    return None, err


def _parse_news_articles(html: str, limit: int) -> list[dict[str, str]]:
    from app.services.yahoo_finance import parse_news_articles_from_html

    return parse_news_articles_from_html(html, limit=limit)


@router.get("/quote", response_model=QuoteResponse)
async def quote_yahoo(ticker: str, _: None = Depends(_auth)):
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


@router.get("/quote/smart", response_model=SmartQuoteResponse)
async def quote_smart(
    ticker: str,
    force_refresh: bool = False,
    _: None = Depends(_auth),
):
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")

    from app.services.yahoo_finance import fetch_yahoo_full

    result = await fetch_yahoo_full(sym, force_refresh=force_refresh)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "extraction_failed"))

    raw = result.get("data") or {}
    mapped = {
        "regularMarketPrice": (
            raw.get("price.regularMarketPrice")
            or raw.get("vision.regularMarketPrice")
            or raw.get("vision.quote_header.regularMarketPrice")
            or raw.get("asp.regularMarketPrice")
            or raw.get("html.regularMarketPrice")
        ),
        "regularMarketChangePercent": (
            raw.get("price.regularMarketChangePercent")
            or raw.get("vision.quote_header.regularMarketChangePercent")
            or raw.get("asp.regularMarketChangePercent")
            or raw.get("html.regularMarketChangePercent")
        ),
        "regularMarketVolume": (
            raw.get("price.regularMarketVolume")
            or raw.get("vision.quote_header.regularMarketVolume")
            or raw.get("asp.regularMarketVolume")
            or raw.get("html.regularMarketVolume")
        ),
        "fiftyTwoWeekHigh": (
            raw.get("summaryDetail.fiftyTwoWeekHigh")
            or raw.get("vision.quote_header.fiftyTwoWeekHigh")
            or raw.get("asp.fiftyTwoWeekHigh")
        ),
        "fiftyTwoWeekLow": (
            raw.get("summaryDetail.fiftyTwoWeekLow")
            or raw.get("vision.quote_header.fiftyTwoWeekLow")
            or raw.get("asp.fiftyTwoWeekLow")
        ),
        "trailingPE": (
            raw.get("summaryDetail.trailingPE")
            or raw.get("defaultKeyStatistics.trailingPE")
            or raw.get("asp.trailingPE")
        ),
        "marketCap": raw.get("summaryDetail.marketCap") or raw.get("asp.marketCap"),
        "shortName": raw.get("price.shortName") or raw.get("asp.shortName") or raw.get("html.shortName"),
    }
    try:
        quote_data = SmartQuoteData.model_validate(mapped)
    except Exception:
        quote_data = None

    return SmartQuoteResponse(
        ok=True,
        ticker=sym,
        data=quote_data,
        cache_hit=result.get("cache_hit", False),
        chunks_used=len(result.get("modules_fetched") or []),
        total_chunks=len(result.get("modules_fetched") or []),
    )


@router.get("/quote/full", response_model=FullQuoteResponse)
async def quote_full(
    ticker: str = "",
    url: str = "",
    force_refresh: bool = False,
    include_html: bool = False,
    _: None = Depends(_auth),
):
    """Fetch Yahoo Finance quote data. Use GET /news for headlines."""
    from app.services.yahoo_finance import fetch_yahoo_full, ticker_from_yahoo_url

    sym = (ticker or "").upper().strip() or ticker_from_yahoo_url(url or "")
    if not sym:
        raise HTTPException(status_code=400, detail="ticker or finance.yahoo.com quote url is required")

    result = await fetch_yahoo_full(
        sym,
        force_refresh=force_refresh,
        include_html=include_html,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "yahoo_fetch_failed"))

    payload = {k: result.get(k) for k in FullQuoteResponse.model_fields if k in result}
    if include_html and result.get("html"):
        payload["html"] = result["html"]
    return FullQuoteResponse(**payload)


@router.post("/finance/scrape")
async def finance_scrape(req: FinanceScrapeRequest, _: None = Depends(_auth)):
    """Scrape all structured data from a Yahoo Finance quote page URL."""
    from app.services.yahoo_finance import fetch_yahoo_full, is_yahoo_quote_url, ticker_from_yahoo_url

    target = (req.url or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="url is required")
    if not is_yahoo_quote_url(target):
        raise HTTPException(status_code=400, detail="url must be a finance.yahoo.com/quote/... page")

    sym = ticker_from_yahoo_url(target)
    if not sym:
        raise HTTPException(status_code=400, detail="could not parse ticker from url")

    result = await fetch_yahoo_full(
        sym,
        force_refresh=req.force_refresh,
        include_html=req.include_html,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "yahoo_fetch_failed"))
    return result


@router.get("/news")
async def stock_news(
    ticker: str,
    limit: int = 8,
    force_refresh: bool = False,
    _: None = Depends(_auth),
):
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")
    limit = max(1, min(limit, 25))

    from app.services.yahoo_finance import fetch_yahoo_news

    result = await fetch_yahoo_news(sym, limit=limit, force_refresh=force_refresh)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "news_fetch_failed"))
    return {
        "ticker": sym,
        "articles": result.get("articles") or [],
        "count": result.get("count", 0),
        "source": result.get("source"),
        "cache_hit": result.get("cache_hit", False),
    }


@router.get("/sec")
async def sec_filing(
    ticker: str,
    form: str = "10-K",
    _: None = Depends(_auth),
):
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")
    form = (form or "10-K").upper().strip()
    url = (
        f"https://efts.sec.gov/LATEST/search-index?q=%22{sym}%22"
        f"&dateRange=custom&startdt=2024-01-01&forms={form}"
    )
    html, err = await _fetch_page_text(url)
    if err and not html:
        raise HTTPException(status_code=502, detail=err)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {"ticker": sym, "form": form, "text": "", "content": ""}
    return {"ticker": sym, "form": form, "text": text[:12_000], "content": text[:12_000]}


@router.get("/fetch/html")
async def fetch_html(
    url: str,
    force_refresh: bool = False,
    _: None = Depends(_auth),
):
    """Fast Tier-1 HTML fetch (httpx). Used by TradeTalk for Slickcharts tables."""
    target_url = (url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="url is required")

    from cache import cache
    from app.services.crawler.compliant_fetcher import fetch_compliant

    cache_key = f"html:{target_url}"
    if not force_refresh:
        cached = await cache.get(cache_key)
        if cached and cached.get("status") == "ok" and cached.get("html"):
            return {
                "ok": True,
                "url": cached.get("url", target_url),
                "html": cached.get("html", ""),
                "http_status": cached.get("http_status"),
                "cache_hit": True,
            }

    result = await fetch_compliant(target_url)
    if result.get("status") != "ok":
        err = result.get("error") or "fetch_failed"
        raise HTTPException(status_code=502, detail=err)

    if not force_refresh:
        await cache.set(cache_key, result)

    return {
        "ok": True,
        "url": result.get("url", target_url),
        "html": result.get("html") or "",
        "http_status": result.get("http_status"),
        "cache_hit": False,
    }


@router.post("/scrape")
@router.post("/crawl")
async def scrape_compat(req: ScrapeRequest, _: None = Depends(_auth)):
    from cache import cache
    from app.services.crawler.compliant_fetcher import fetch_compliant
    from app.services.yahoo_finance import fetch_yahoo_full, is_yahoo_quote_url, ticker_from_yahoo_url
    from crawler import crawl_single

    target_url = (req.url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="url is required")

    # Yahoo Finance quote pages: return structured data (not just HTML shell).
    if is_yahoo_quote_url(target_url):
        sym = ticker_from_yahoo_url(target_url)
        if sym:
            yahoo = await fetch_yahoo_full(sym, force_refresh=req.force_refresh)
            if yahoo.get("ok"):
                return {
                    "url": target_url,
                    "status": "ok",
                    "status_code": 200,
                    "title": yahoo.get("data", {}).get("price.shortName") or sym,
                    "ticker": sym,
                    "source": yahoo.get("source"),
                    "field_count": yahoo.get("field_count", 0),
                    "modules_fetched": yahoo.get("modules_fetched", []),
                    "yahoo_data": yahoo.get("data", {}),
                    "excerpt": json.dumps(yahoo.get("data", {}))[:2000],
                }

    if not req.force_refresh:
        cached = await cache.get(target_url)
        if cached:
            return cached

    result = await fetch_compliant(target_url)
    if result.get("status") != "ok":
        result = await crawl_single(target_url)

    from app.services.crawler.vision_fetcher import maybe_apply_vision_fallback, vision_fallback_enabled

    if vision_fallback_enabled():
        result = await maybe_apply_vision_fallback(
            result if isinstance(result, dict) else {"status": "error", "url": target_url},
            target_url,
            task="finance",
        )

    if result.get("status") == "ok":
        await cache.set(target_url, result)
    if result.get("html") and not result.get("excerpt"):
        result["excerpt"] = (result.get("text") or "")[:400]
    if result.get("vision_data") and not result.get("excerpt"):
        import json as _json

        result["excerpt"] = _json.dumps(result["vision_data"])[:2000]
    if result.get("http_status") is not None and "status_code" not in result:
        result["status_code"] = result["http_status"]
    if result.get("status") != "ok" and not result.get("vision_data"):
        raise HTTPException(status_code=502, detail=result.get("error") or "scrape_failed")
    return result


@router.delete("/cache")
async def clear_cache(_: None = Depends(_auth)):
    from cache import cache

    await cache.clear_all()
    return {"status": "cache cleared"}


# Cards endpoints removed in shopping-intel refactor.
@router.post("/cards/recommend")
@router.post("/cards/points-usage")
@router.post("/extract")
async def legacy_cards_removed():
    raise HTTPException(
        410,
        detail="Cards/extract endpoints removed. Use /quote/full, /finance/scrape, /quote, /news, or /sec.",
    )
