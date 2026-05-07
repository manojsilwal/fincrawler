# cards_crawler.py
"""
Crawler for Credit Cards and Points Usage.

Uses Google Search to find real-time recommendations for credit cards
and best usage strategies for reward points, then extracts structured
data using DeepSeek v4 Pro.
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_CARDS_PROMPT = """\
This is a search results page for best credit cards for the category: "{category}".
Extract the top recommended credit cards mentioned on the page.
Return a JSON object with a single key "cards" containing an array.
Each card object must have these fields:
  card_name      (str)          — e.g. "Chase Sapphire Preferred"
  issuer         (str)          — e.g. "Chase", "American Express"
  annual_fee     (float|null)   — current annual fee in USD (numbers only, no $)
  welcome_offer  (str|null)     — current sign-up bonus / initial offer
  earning_rates  (str)          — summary of points/cashback earning rates
  pros           (list[str])    — list of 1-3 pros/benefits
  cons           (list[str])    — list of 1-3 cons/drawbacks

Rules:
- Include up to 5 of the most highly recommended cards.
- Use null for any field not explicitly mentioned or clearly visible.
- Return only the JSON object, no markdown fences.\
"""

_POINTS_PROMPT = """\
This is a search results page for the best ways to use "{points_program}" points for "{spend_category}".
Extract the top recommended strategies or redemption sweet spots.
Return a JSON object with a single key "strategies" containing an array.
Each strategy object must have these fields:
  title          (str)          — A short title for the strategy (e.g. "Transfer to Hyatt for Luxury Hotels")
  description    (str)          — A 1-2 sentence explanation of how it works and why it's good
  estimated_cpp  (float|null)   — Estimated value in cents-per-point (CPP) if mentioned (numbers only)
  transfer_partners (list[str]) — List of airlines or hotels involved in the transfer (e.g. ["Hyatt", "Virgin Atlantic"])

Rules:
- Include up to 5 distinct strategies.
- Use null for estimated_cpp if a specific numerical value is not given.
- Return only the JSON object, no markdown fences.\
"""

# ---------------------------------------------------------------------------
# Core crawl
# ---------------------------------------------------------------------------

async def _human_delay(min_ms: int = 600, max_ms: int = 1800) -> None:
    import random
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

async def _dismiss_google_consent(page) -> None:
    for sel in (
        "button#L2AGLb",
        "[aria-label='Accept all']",
        "button[jsname='higCR']",
        ".sy4vM",
    ):
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=2_000)
                await _human_delay(300, 600)
                return
        except Exception:
            pass

async def _scroll_partial(page) -> None:
    await page.evaluate("""
        async () => {
            await new Promise(resolve => {
                let pos = 0;
                const target = document.body.scrollHeight * 0.8;
                const tick = () => {
                    const step = 200 + Math.floor(Math.random() * 100);
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

async def _crawl_google_search(query: str) -> dict:
    """Fetch Google Search results for *query*. Returns crawl dict."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}&hl=en&gl=us"
    crawled_at = datetime.now(timezone.utc).isoformat()

    if pool._browser is None:
        return {"status": "error", "error": "browser_pool_not_ready", "url": url}

    try:
        context_kwargs = get_stealth_context_kwargs()
        context = await pool._browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            await apply_stealth(page)
            logger.info("Google Search crawl → %s", url)

            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            http_status = resp.status if resp else None

            await _human_delay(1000, 2000)
            await _dismiss_google_consent(page)
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
            page_text = "\\n".join(cleaned)[:100_000]

            logger.info("Google Search captured %d chars, http=%s", len(page_text), http_status)
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
        logger.warning("Google Search timeout")
        return {"url": url, "status": "error", "error": "timeout", "crawled_at": crawled_at}
    except Exception as exc:
        logger.exception("Google Search unexpected error")
        return {"url": url, "status": "error", "error": str(exc), "crawled_at": crawled_at}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_card_recommendations(category: str) -> dict:
    """Search and extract best credit cards for a category."""
    query = f"best credit cards for {category} annual fee sign up bonus"
    crawl = await _crawl_google_search(query)

    if crawl["status"] != "ok":
        return {
            "status": "error",
            "category": category,
            "cards": [],
            "error": crawl.get("error", "crawl_failed"),
        }

    prompt = _CARDS_PROMPT.format(category=category)
    extracted = await extract_structured(
        page_text=crawl["page_text"],
        prompt=prompt,
        extra_context=f"Extracting credit card recommendations for: {category}",
    )

    cards = []
    if "_error" not in extracted:
        raw_list = extracted.get("cards", [])
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, dict) and item.get("card_name"):
                    cards.append(item)

    return {
        "status": "ok",
        "category": category,
        "url": crawl["url"],
        "cards": cards,
        "total_cards": len(cards),
        "crawled_at": crawl.get("crawled_at", ""),
    }

async def search_points_usage(points_program: str, spend_category: str) -> dict:
    """Search and extract best usage strategies for a points program."""
    query = f"best ways to use {points_program} points for {spend_category}"
    crawl = await _crawl_google_search(query)

    if crawl["status"] != "ok":
        return {
            "status": "error",
            "points_program": points_program,
            "spend_category": spend_category,
            "strategies": [],
            "error": crawl.get("error", "crawl_failed"),
        }

    prompt = _POINTS_PROMPT.format(points_program=points_program, spend_category=spend_category)
    extracted = await extract_structured(
        page_text=crawl["page_text"],
        prompt=prompt,
        extra_context=f"Extracting points usage strategies for {points_program} on {spend_category}",
    )

    strategies = []
    if "_error" not in extracted:
        raw_list = extracted.get("strategies", [])
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, dict) and item.get("title"):
                    strategies.append(item)

    return {
        "status": "ok",
        "points_program": points_program,
        "spend_category": spend_category,
        "url": crawl["url"],
        "strategies": strategies,
        "total_strategies": len(strategies),
        "crawled_at": crawl.get("crawled_at", ""),
    }
