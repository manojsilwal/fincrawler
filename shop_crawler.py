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
from stealth import apply_stealth, get_stealth_context_kwargs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retailer config
# ---------------------------------------------------------------------------

RETAILERS: dict[str, dict] = {
    "amazon": {
        "name": "Amazon",
        "search_url": "https://www.amazon.com/s?k={query}&ref=nb_sb_noss",
        "wait_selector": "#search, #s-results-list-atf, [data-component-type='s-search-result']",
        "consent_selectors": ["#sp-cc-accept", "[data-cel-widget='sp-cc-accept']"],
    },
    "walmart": {
        "name": "Walmart",
        "search_url": "https://www.walmart.com/search?q={query}",
        "wait_selector": "[data-automation-id='product-title'], .search-result-product-title",
        "consent_selectors": ["[data-automation='close-cta']", "#onetrust-accept-btn-handler"],
    },
    "ebay": {
        "name": "eBay",
        "search_url": "https://www.ebay.com/sch/i.html?_nkw={query}&_sacat=0",
        "wait_selector": ".s-item__title, #srp-river-results",
        "consent_selectors": ["#gdpr-banner-accept", ".gh-consent-btn-accept"],
    },
    "bestbuy": {
        "name": "Best Buy",
        "search_url": "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
        "wait_selector": ".sku-title, .sr-only, .priceView-hero-price",
        "consent_selectors": [".us-link", "#accept-cookie-btn"],
    },
    "target": {
        "name": "Target",
        "search_url": "https://www.target.com/s?searchTerm={query}",
        "wait_selector": "[data-test='product-title'], .ProductCardVariantDefault-module__title",
        "consent_selectors": ["#accept", "[data-test='age-verification-button']"],
    },
}

# ---------------------------------------------------------------------------
# LLM extraction prompt for shopping data
# ---------------------------------------------------------------------------

_SHOP_PROMPT_TEMPLATE = """\
Extract shopping/product information for "{query}" from this retail search results page.
Find the FIRST matching product and extract all available fields.
Return a single JSON object with:
  product_name (str), price (float, USD), original_price (float or null if no discount),
  discount_pct (float or null), currency (str, default "USD"),
  availability (str: "In Stock" | "Out of Stock" | "Limited" | "Unknown"),
  seller (str, the retailer name), rating (float or null), review_count (int or null),
  product_url (str or null), savings (float or null).
Only return the JSON object. Use null for unavailable fields. Do not invent data.\
"""

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

async def _crawl_retailer(retailer_key: str, query: str) -> dict:
    """Crawl a single retailer's search results for *query*."""
    cfg = RETAILERS.get(retailer_key)
    if not cfg:
        return {"retailer": retailer_key, "status": "error", "error": "unknown_retailer"}

    encoded = urllib.parse.quote_plus(query)
    url = cfg["search_url"].format(query=encoded)
    crawled_at = datetime.now(timezone.utc).isoformat()

    if pool._browser is None:
        return {"retailer": retailer_key, "status": "error", "error": "browser_pool_not_ready"}

    try:
        context_kwargs = get_stealth_context_kwargs()
        context = await pool._browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            # Inject stealth JS before any navigation
            await apply_stealth(page)

            logger.info("Shop crawl [%s] → %s", retailer_key, url)

            # Navigate with a generous timeout
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
            http_status = resp.status if resp else None

            # Initial human delay after page load
            await _human_delay(1200, 2500)

            # Dismiss cookie banners
            await _dismiss_consent(page, cfg.get("consent_selectors", []))

            # Check for blocks
            block_reason = await _detect_block(page)
            if block_reason in ("cloudflare_challenge", "captcha"):
                # Wait extra for JS-rendered challenge to resolve
                logger.warning("[%s] Challenge detected (%s), waiting 8s…", retailer_key, block_reason)
                await page.wait_for_timeout(8_000)
                block_reason = await _detect_block(page)

            # Try to wait for product elements
            try:
                await page.wait_for_selector(
                    cfg["wait_selector"],
                    timeout=12_000,
                    state="visible",
                )
            except Exception:
                logger.debug("[%s] Product selector not found within timeout", retailer_key)

            # Human-like scroll to load lazy images/prices
            await _human_scroll(page)

            # Re-check for block after JS has settled
            final_block = await _detect_block(page)
            if final_block in ("captcha", "access_denied", "blocked"):
                return {
                    "retailer": cfg["name"],
                    "retailer_key": retailer_key,
                    "query": query,
                    "url": url,
                    "status": "blocked",
                    "block_reason": final_block,
                    "http_status": http_status,
                    "crawled_at": crawled_at,
                }

            # Extract visible text
            raw_text = await page.inner_text("body")

            # Light clean — collapse blanks, cap at 80K (plenty for product lists)
            lines = raw_text.splitlines()
            cleaned_lines = []
            prev_blank = False
            for line in lines:
                s = line.strip()
                blank = s == ""
                if blank and prev_blank:
                    continue
                cleaned_lines.append(s)
                prev_blank = blank
            page_text = "\n".join(cleaned_lines)[:80_000]

            char_count = len(page_text)
            logger.info("[%s] Captured %d chars, http=%s", retailer_key, char_count, http_status)

            return {
                "retailer": cfg["name"],
                "retailer_key": retailer_key,
                "query": query,
                "url": url,
                "page_text": page_text,
                "char_count": char_count,
                "http_status": http_status,
                "status": "ok",
                "crawled_at": crawled_at,
            }

        finally:
            await context.close()

    except PlaywrightTimeout:
        logger.warning("[%s] Timeout", retailer_key)
        return {
            "retailer": cfg["name"],
            "retailer_key": retailer_key,
            "query": query,
            "url": url,
            "status": "error",
            "error": "timeout",
            "crawled_at": crawled_at,
        }
    except Exception as exc:
        logger.exception("[%s] Unexpected error", retailer_key)
        return {
            "retailer": cfg["name"],
            "retailer_key": retailer_key,
            "query": query,
            "url": url,
            "status": "error",
            "error": str(exc),
            "crawled_at": crawled_at,
        }


# ---------------------------------------------------------------------------
# LLM extraction on top of crawl
# ---------------------------------------------------------------------------

async def _extract_shop_result(crawl: dict, query: str) -> dict:
    """Run LLM extraction on a successful crawl result."""
    if crawl["status"] != "ok":
        return {**crawl, "data": None}

    prompt = _SHOP_PROMPT_TEMPLATE.format(query=query)
    extracted = await extract_structured(
        page_text=crawl["page_text"],
        prompt=prompt,
        extra_context=f"Retailer: {crawl['retailer']}. Product search: {query}",
    )

    # Attach without the raw page_text blob (too large for response)
    result = {k: v for k, v in crawl.items() if k != "page_text"}
    result["data"] = extracted
    result["llm_extraction"] = "_error" not in extracted
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_product(
    query: str,
    retailers: Optional[list[str]] = None,
    max_concurrency: int = 3,
    google_fallback: bool = True,
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
            crawl = await _crawl_retailer(rkey, query)
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
