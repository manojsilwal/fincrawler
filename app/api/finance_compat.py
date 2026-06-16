"""TradeTalk finance endpoints — quote, news, SEC, legacy scrape, Firecrawl /v1/scrape."""

from __future__ import annotations

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


class ScrapeRequest(BaseModel):
    url: str
    force_refresh: bool = False


def _parse_yahoo_regular_price(page_text: str) -> Optional[float]:
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


async def _fetch_yahoo_quote_price(url: str) -> tuple[Optional[float], Optional[str]]:
    html, err = await _fetch_page_text(url)
    if err and not html:
        return None, err
    price = _parse_yahoo_regular_price(html)
    if price is not None:
        return price, None
    return None, err


def _parse_news_articles(html: str, limit: int) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href="([^"]+)"[^>]*>[\s\S]{0,200}?<h3[^>]*>([^<]{8,240})</h3>',
        html,
        re.I,
    ):
        link, title = m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip()
        if title in seen:
            continue
        seen.add(title)
        articles.append(
            {
                "title": title,
                "summary": "",
                "link": link,
                "publisher": "Yahoo Finance",
            }
        )
        if len(articles) >= limit:
            break
    if articles:
        return articles

    for m in re.finditer(r"<h3[^>]*>([^<]{12,240})</h3>", html, re.I):
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title in seen:
            continue
        seen.add(title)
        articles.append(
            {
                "title": title,
                "summary": "",
                "link": "",
                "publisher": "Yahoo Finance",
            }
        )
        if len(articles) >= limit:
            break
    return articles


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

    from extractor import extract_quote

    result = await extract_quote(ticker=sym, force_refresh=force_refresh)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error", "extraction_failed"))

    raw_data = result.get("data", {})
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


@router.get("/news")
async def stock_news(
    ticker: str,
    limit: int = 8,
    _: None = Depends(_auth),
):
    sym = (ticker or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="ticker is required")
    limit = max(1, min(limit, 25))
    url = f"https://finance.yahoo.com/quote/{sym}/news"
    html, err = await _fetch_page_text(url)
    if err and not html:
        raise HTTPException(status_code=502, detail=err)
    articles = _parse_news_articles(html, limit)
    return {"ticker": sym, "articles": articles, "count": len(articles)}


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


@router.post("/scrape")
@router.post("/crawl")
async def scrape_compat(req: ScrapeRequest, _: None = Depends(_auth)):
    from cache import cache
    from crawler import crawl_single

    target_url = (req.url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="url is required")

    if not req.force_refresh:
        cached = await cache.get(target_url)
        if cached:
            return cached

    result = await crawl_single(target_url)
    if result.get("status") == "ok":
        await cache.set(target_url, result)
    if result.get("html") and not result.get("excerpt"):
        result["excerpt"] = (result.get("text") or "")[:400]
    if result.get("http_status") is not None and "status_code" not in result:
        result["status_code"] = result["http_status"]
    if result.get("status") != "ok":
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
        detail="Cards/extract endpoints removed. Use /quote, /news, /sec, or /v1/scrape.",
    )
