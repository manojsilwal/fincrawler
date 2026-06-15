# google_shop.py
"""
Google Shopping fallback scraper.

When individual retailer sites block direct crawls, Google Shopping
aggregates their prices in a single, much-more-scraping-friendly page.

One Google Shopping search returns listings from:
  Amazon, Walmart, eBay, Best Buy, Target, and dozens more — simultaneously.

Strategy
--------
1. Navigate to https://www.google.com/search?q={query}&tbm=shop
2. Stealth browser with full JS patch (same as shop_crawler.py)
3. Scroll to load lazy-rendered price cards
4. Extract all visible text
5. DeepSeek v4 Pro → JSON array of {retailer, price, …} for every listing
6. Map back to our canonical retailer keys

Usage
-----
from google_shop import google_shop_search

results = await google_shop_search("DJI Osmo Pocket 3")
# → list of {"retailer": "Amazon", "price": 499.0, "availability": "In Stock", …}
"""

import asyncio
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout

from browser_pool import pool
from llm import extract_structured
from stealth import apply_stealth, get_stealth_context_kwargs

from shop_price_extract import (
    extract_google_listings_from_page,
    price_rich_excerpt,
    prepare_llm_context,
    shop_result_missing_price,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google Shopping URL templates
# ---------------------------------------------------------------------------
_GOOGLE_SHOP_URL     = "https://www.google.com/search?q={query}&tbm=shop&hl=en&gl=us"
_GOOGLE_SHOP_URL_ALT = "https://www.google.com/search?q={query}+buy+price&tbm=shop&hl=en"

# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------
_GOOGLE_SHOP_PROMPT = """This is a Google Shopping search results page for "{query}".
Extract ALL product listings visible on the page for the exact product "{query}".

CRITICAL RULES:
- ONLY include the actual product requested.
- EXCLUDE accessories, cases, protection plans, and unrelated items.
- price must be a JSON number (e.g. 419.00), not a string.

Return exactly this JSON shape:
{{"listings": [
  {{"retailer": "Walmart", "price": 419.00, "product_name": "...", "availability": "In stock", "product_url": null}}
]}}

Include every valid listing you can see. Use null for missing optional fields."""

# ---------------------------------------------------------------------------
# Retailer name normalisation → canonical keys
# ---------------------------------------------------------------------------
_RETAILER_ALIASES: dict[str, str] = {
    "amazon":       "amazon",
    "amazon.com":   "amazon",
    "walmart":      "walmart",
    "walmart.com":  "walmart",
    "ebay":         "ebay",
    "ebay.com":     "ebay",
    "best buy":     "bestbuy",
    "bestbuy":      "bestbuy",
    "bestbuy.com":  "bestbuy",
    "target":       "target",
    "target.com":   "target",
}

def _normalise_retailer(name: str) -> str:
    """Map retailer display name → canonical key (best-effort)."""
    return _RETAILER_ALIASES.get(name.lower().strip(), name.lower().strip())


# ---------------------------------------------------------------------------
# Human-like helpers (same approach as shop_crawler)
# ---------------------------------------------------------------------------

async def _human_delay(min_ms: int = 600, max_ms: int = 1800) -> None:
    import random
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _scroll_partial(page) -> None:
    """Scroll ~60% down to expose lazy-loaded price cards."""
    await page.evaluate("""
        async () => {
            await new Promise(resolve => {
                let pos = 0;
                const target = document.body.scrollHeight * 0.65;
                const tick = () => {
                    const step = 150 + Math.floor(Math.random() * 100);
                    window.scrollBy(0, step);
                    pos += step;
                    if (pos < target) setTimeout(tick, 60 + Math.floor(Math.random() * 80));
                    else resolve();
                };
                setTimeout(tick, 300);
            });
        }
    """)
    await _human_delay(500, 1000)


async def _dismiss_google_consent(page) -> None:
    """Dismiss Google's cookie consent / GDPR banner if present."""
    for sel in (
        "button#L2AGLb",           # "Accept all" (Google)
        "[aria-label='Accept all']",
        "button[jsname='higCR']",
        ".sy4vM",
    ):
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=2_000)
                await _human_delay(300, 600)
                logger.debug("Google consent dismissed via: %s", sel)
                return
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core crawl
# ---------------------------------------------------------------------------

async def _crawl_google_shopping(query: str) -> dict:
    """Fetch Google Shopping page for *query*. Returns crawl dict."""
    encoded = urllib.parse.quote_plus(query)
    url = _GOOGLE_SHOP_URL.format(query=encoded)
    crawled_at = datetime.now(timezone.utc).isoformat()

    if pool._browser is None:
        return {"status": "error", "error": "browser_pool_not_ready", "url": url}

    try:
        context_kwargs = get_stealth_context_kwargs()
        context = await pool._browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            await apply_stealth(page)
            logger.info("Google Shopping crawl → %s", url)

            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            http_status = resp.status if resp else None

            await _human_delay(1000, 2000)
            await _dismiss_google_consent(page)

            # Wait for product cards to appear
            try:
                await page.wait_for_selector(
                    ".sh-dgr__grid-result, .sh-pr__product-results, [data-hveid]",
                    timeout=10_000,
                    state="visible",
                )
            except Exception:
                logger.debug("Google Shopping product selector not found in time")

            await _scroll_partial(page)

            raw_text = await page.inner_text("body")

            # Collapse blank lines
            lines = raw_text.splitlines()
            cleaned = []
            prev_blank = False
            for line in lines:
                s = line.strip()
                blank = s == ""
                if blank and prev_blank:
                    continue
                cleaned.append(s)
                prev_blank = blank
            page_text = "\n".join(cleaned)[:100_000]

            logger.info("Google Shopping captured %d chars, http=%s", len(page_text), http_status)
            return {
                "url": url,
                "page_text": page_text,
                "char_count": len(page_text),
                "http_status": http_status,
                "status": "ok",
                "crawled_at": crawled_at,
            }

        finally:
            await context.close()

    except PlaywrightTimeout:
        logger.warning("Google Shopping timeout")
        return {"url": url, "status": "error", "error": "timeout", "crawled_at": crawled_at}
    except Exception as exc:
        logger.exception("Google Shopping unexpected error")
        return {"url": url, "status": "error", "error": str(exc), "crawled_at": crawled_at}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def google_shop_search(
    query: str,
    max_listings: int = 20,
    crawl_options: dict | None = None,
) -> dict:
    """
    Search Google Shopping for *query* and return structured product listings.

    Parameters
    ----------
    query:
        Product name, e.g. "DJI Osmo Pocket 3"
    max_listings:
        Maximum listings to return (LLM will try to extract up to this many).

    Returns
    -------
    dict with keys:
        status           "ok" | "error"
        source           "google_shopping"
        query            str
        url              str
        listings         list[dict]  — each has retailer, price, availability, …
        total_listings   int
        char_count       int
        crawled_at       str
        error            str  (only on error)
    """
    from tier_router import fetch_retailer

    crawl = await fetch_retailer("google_shopping", query, crawl_options)

    if crawl["status"] != "ok":
        return {
            "status": "error",
            "source": "google_shopping",
            "query": query,
            "url": crawl.get("url", ""),
            "listings": [],
            "total_listings": 0,
            "error": crawl.get("error", crawl.get("block_reason", "crawl_failed")),
            "tier_used": crawl.get("tier_used"),
            "tier_name": crawl.get("tier_name"),
            "detection_hits": crawl.get("detection_hits", []),
            "session_id": crawl.get("session_id"),
        }

    page_text = crawl.get("page_text") or crawl.get("text") or ""
    html = crawl.get("html") or ""
    llm_context = price_rich_excerpt(page_text, html)
    if len(llm_context) < 500:
        llm_context, _ = prepare_llm_context(page_text, html, query, "google_shopping")
    prompt = _GOOGLE_SHOP_PROMPT.format(query=query)
    extracted = await extract_structured(
        page_text=llm_context,
        prompt=prompt,
        extra_context=f"Product search query: {query}",
        task="shopping",
    )

    listings = []
    if "_error" not in extracted:
        raw_list = extracted.get("listings", [])
        if isinstance(raw_list, list):
            for item in raw_list[:max_listings]:
                if isinstance(item, dict) and item.get("price"):
                    item["retailer_key"] = _normalise_retailer(item.get("retailer", ""))
                    item.setdefault("price_source", "llm")
                    listings.append(item)
        elif isinstance(extracted, dict) and "price" in extracted:
            extracted["retailer_key"] = _normalise_retailer(extracted.get("retailer", ""))
            extracted.setdefault("price_source", "llm")
            listings = [extracted]

    if not listings:
        listings = extract_google_listings_from_page(page_text, html, query)[:max_listings]
        if listings:
            logger.info("Google Shopping regex fallback found %d listings", len(listings))

    listings.sort(key=lambda x: x.get("price") or float("inf"))

    return {
        "status": "ok",
        "source": "google_shopping",
        "query": query,
        "url": crawl["url"],
        "listings": listings,
        "total_listings": len(listings),
        "char_count": crawl.get("char_count", 0),
        "crawled_at": crawl.get("crawled_at", ""),
        "tier_used": crawl.get("tier_used"),
        "tier_name": crawl.get("tier_name"),
        "detection_hits": crawl.get("detection_hits", []),
        "session_id": crawl.get("session_id"),
    }


def enrich_with_google(
    retailer_results: list[dict],
    google_listings: list[dict],
) -> list[dict]:
    """
    For any blocked/errored retailer result, find a matching listing
    from Google Shopping data and backfill the data field.

    Parameters
    ----------
    retailer_results:
        Output from shop_crawler.search_product()
    google_listings:
        The .listings list from google_shop_search()

    Returns
    -------
    The same list, with blocked entries enriched from Google data.
    """
    # Build lookup: canonical_key → best Google listing
    google_by_key: dict[str, dict] = {}
    for listing in google_listings:
        rkey = listing.get("retailer_key", "")
        if rkey and rkey not in google_by_key:
            google_by_key[rkey] = listing

    enriched = []
    for r in retailer_results:
        rkey = r.get("retailer_key", "")
        needs_google = shop_result_missing_price(r) and rkey in google_by_key
        if r.get("status") in ("blocked", "error") and rkey in google_by_key:
            needs_google = True

        if needs_google and rkey in google_by_key:
            g = google_by_key[rkey]
            r = {
                **r,
                "data": {**g, "price_source": "google_shopping"},
                "status": "ok_via_google",
                "llm_extraction": True,
            }
            logger.info("Enriched %s from Google Shopping", r.get("retailer", rkey))
        enriched.append(r)
    return enriched
