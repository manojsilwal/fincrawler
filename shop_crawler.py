# shop_crawler.py
"""
Shopping-aware crawler.

Pipeline per retailer
---------------------
1. Open stealth browser context (randomised UA, viewport, full JS patch)
2. Navigate to the retailer's search URL for the query
3. Human-like delay + incremental scroll to trigger lazy-loaded prices
4. Auto-dismiss cookie/consent banners
5. Extract visible text and pass to DeepSeek LLM for structured parsing
6. Return typed ShopResult dict

Supported retailers
-------------------
amazon, walmart, ebay, bestbuy, target
"""

import asyncio
import logging
import random
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout

from browser_pool import pool
from llm import extract_structured
from shop_price_extract import (
    crawl_likely_blocked,
    merge_shop_extraction,
    prepare_llm_context,
    retailer_prompt_hint,
)
from stealth import apply_stealth, get_stealth_context_kwargs

from profiles import retailers_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retailer config (loaded from profiles/retailers.json)
# ---------------------------------------------------------------------------

RETAILERS: dict[str, dict] = retailers_dict()

# ---------------------------------------------------------------------------
# LLM extraction prompt for shopping data
# ---------------------------------------------------------------------------

_SHOP_PROMPT_TEMPLATE = """ Extract shopping/product information for "{query}" from this retail search results page.
Find the MOST RELEVANT match for the actual device/item.
CRITICAL RULES:
- IGNORE accessories (cases, screen protectors, cables, straps, batteries).
- IGNORE protection plans or extended warranties.
- IGNORE "sponsored" results if they do not match the query exactly.
- If the first result is an accessory, skip it and find the actual product.
- If the device is not found, return an object with "_error": "product_not_found".

Return a single JSON object with:
  product_name (str), price (float, USD), original_price (float or null),
  discount_pct (float or null), currency (str, default "USD"),
  availability (str), seller (str), rating (float or null), review_count (int or null),
  product_url (str or null), savings (float or null).
Only return the JSON object."""

# ---------------------------------------------------------------------------
# Human-like helpers
# ---------------------------------------------------------------------------

async def _human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _human_scroll(page) -> None:
    """Gradual scroll that mimics a real user reading the page."""
    await page.evaluate("""
        async () => {
            await new Promise((resolve) => {
                let pos = 0;
                const step = () => {
                    const delta = Math.floor(Math.random() * 180) + 60;
                    window.scrollBy(0, delta);
                    pos += delta;
                    if (pos < document.body.scrollHeight * 0.7) {
                        setTimeout(step, Math.floor(Math.random() * 120) + 40);
                    } else {
                        resolve();
                    }
                };
                setTimeout(step, 200);
            });
        }
    """)
    await _human_delay(600, 1200)


async def _dismiss_consent(page, selectors: list[str]) -> None:
    """Try to click any cookie/consent banners."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=3_000)
                await _human_delay(400, 800)
                logger.debug("Dismissed consent banner: %s", sel)
                return
        except Exception:
            continue


async def _detect_block(page) -> Optional[str]:
    """Return a block reason string if we hit a challenge/bot page."""
    title = (await page.title()).lower()
    url   = page.url.lower()
    body  = ""
    try:
        body = (await page.inner_text("body"))[:1000].lower()
    except Exception:
        pass

    if "robot" in body or "captcha" in body:
        return "captcha"
    if "access denied" in title or "access denied" in body:
        return "access_denied"
    if "cloudflare" in body or "just a moment" in title:
        return "cloudflare_challenge"
    if "blocked" in title or "blocked" in body[:200]:
        return "blocked"
    if "sign in" in title and "search" not in url:
        return "login_wall"
    return None


# ---------------------------------------------------------------------------
# Core per-retailer crawl
# ---------------------------------------------------------------------------

async def _crawl_retailer(retailer_key: str, query: str, crawl_options: dict | None = None) -> dict:
    """Crawl a single retailer's search results via tier_router."""
    from tier_router import fetch_retailer

    return await fetch_retailer(retailer_key, query, crawl_options)


# ---------------------------------------------------------------------------
# LLM extraction on top of crawl
# ---------------------------------------------------------------------------

async def _extract_shop_result(crawl: dict, query: str) -> dict:
    """Run LLM extraction on a successful crawl result."""
    if crawl["status"] != "ok":
        return {**crawl, "data": None}

    if crawl_likely_blocked(crawl):
        logger.warning(
            "[%s] Treating thin/challenge page as blocked (chars=%s)",
            crawl.get("retailer_key"),
            crawl.get("char_count"),
        )
        result = {k: v for k, v in crawl.items() if k not in ("page_text", "html")}
        result["status"] = "blocked"
        result["block_reason"] = crawl.get("block_reason") or "bot_challenge"
        result["data"] = None
        return result

    retailer_key = crawl.get("retailer_key", "")
    page_text = crawl.get("page_text") or crawl.get("text") or ""
    html = crawl.get("html") or ""

    llm_context, price_candidates = prepare_llm_context(
        page_text, html, query, retailer_key
    )

    hint = retailer_prompt_hint(retailer_key)
    prompt = _SHOP_PROMPT_TEMPLATE.format(query=query)
    if hint:
        prompt = f"{hint}\n\n{prompt}"

    extracted = await extract_structured(
        page_text=llm_context,
        prompt=prompt,
        extra_context=f"Retailer: {crawl.get('retailer', retailer_key)}. Product search: {query}",
        task="shopping",
    )

    extracted = merge_shop_extraction(
        extracted,
        price_candidates,
        query=query,
        retailer_key=retailer_key,
    )

    # Basic name-matching validation to prevent accessory hallucination
    if "_error" not in extracted and extracted.get("product_name"):
        name = extracted["product_name"].lower()
        q = query.lower()
        query_words = [w for w in q.split() if len(w) > 2]
        matches = sum(1 for w in query_words if w in name)
        price = extracted.get("price")
        if (
            matches < len(query_words) / 2
            and price is not None
            and float(price) < 50
            and extracted.get("price_source") != "regex"
        ):
            logger.warning(
                "[%s] Extraction rejected: product name mismatch or suspiciously low price for %s",
                retailer_key,
                query,
            )
            extracted = {
                "_error": "product_name_mismatch",
                "product_name": extracted["product_name"],
                "price": extracted["price"],
            }
            # Regex backfill after rejection
            extracted = merge_shop_extraction(
                extracted,
                price_candidates,
                query=query,
                retailer_key=retailer_key,
            )

    # Attach without the raw page_text/html blobs (too large for response)
    result = {
        k: v for k, v in crawl.items() if k not in ("page_text", "html")
    }
    result["data"] = extracted
    result["llm_extraction"] = "_error" not in extracted or bool(extracted.get("price"))
    if price_candidates:
        result["price_candidates_usd"] = price_candidates
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_product(
    query: str,
    retailers: Optional[list[str]] = None,
    max_concurrency: int = 1,
    google_fallback: bool = True,
    crawl_options: dict | None = None,
) -> list[dict]:
    """
    Search for *query* across multiple retailers and return structured price data.

    Parameters
    ----------
    query:
        Product name, e.g. "DJI Osmo Pocket 3"
    retailers:
        Subset of ["amazon","walmart","ebay","bestbuy","target"].
        Defaults to all five.
    max_concurrency:
        Max simultaneous browser contexts (each is heavy — keep ≤ 3).
    google_fallback:
        When True (default), runs a Google Shopping crawl in parallel
        and uses it to fill in prices for any blocked/errored retailers.

    Returns
    -------
    list of result dicts, one per retailer, with fields:
        retailer, retailer_key, query, url, status, data (structured LLM output),
        http_status, crawled_at, char_count, block_reason (if blocked)
    """
    from google_shop import google_shop_search, enrich_with_google

    target_retailers = retailers or list(RETAILERS.keys())
    target_retailers = [r for r in target_retailers if r in RETAILERS]

    sem = asyncio.Semaphore(max_concurrency)

    async def _run_one(rkey: str) -> dict:
        async with sem:
            crawl = await _crawl_retailer(rkey, query, crawl_options)
            return await _extract_shop_result(crawl, query)

    # Run direct retailer crawls + Google Shopping in parallel
    if google_fallback:
        retailer_tasks = [_run_one(r) for r in target_retailers]
        google_task    = google_shop_search(query)
        all_results    = await asyncio.gather(
            *retailer_tasks, google_task,
            return_exceptions=False,
        )
        retailer_results = list(all_results[:-1])
        google_result    = all_results[-1]

        # Enrich blocked/errored entries with Google data
        google_listings = google_result.get("listings", []) if isinstance(google_result, dict) else []
        retailer_results = enrich_with_google(retailer_results, google_listings)

        # Attach the raw Google Shopping result as an extra entry
        retailer_results.append({
            "retailer":      "Google Shopping",
            "retailer_key":  "google_shopping",
            "query":         query,
            "url":           google_result.get("url", "") if isinstance(google_result, dict) else "",
            "status":        google_result.get("status", "error") if isinstance(google_result, dict) else "error",
            "data":          {"listings": google_listings},
            "total_listings": google_result.get("total_listings", 0) if isinstance(google_result, dict) else 0,
            "char_count":    google_result.get("char_count", 0) if isinstance(google_result, dict) else 0,
            "llm_extraction": bool(google_listings),
            "crawled_at":    google_result.get("crawled_at", "") if isinstance(google_result, dict) else "",
        })
        return retailer_results
    else:
        tasks = [_run_one(r) for r in target_retailers]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)
