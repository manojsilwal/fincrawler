# crawler.py
"""
Core crawl logic.
crawl_single  — scrapes one URL using a pooled Playwright browser
crawl_parallel — fans out crawl_single across a list of URLs concurrently
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser_pool import pool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_NAV_TIMEOUT_MS  = 30_000   # 30 s page-load hard limit
_SCROLL_DELAY_MS = 800      # wait after scroll to trigger lazy-load
# Raised from 50 K → 200 K: the extractor pipeline handles chunking/retrieval;
# raw crawl should preserve as much content as possible for long docs (SEC 10-K).
_MAX_TEXT_CHARS  = 200_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """Strip excess whitespace / blank lines from extracted page text.

    Collapses duplicate blank lines but preserves paragraph structure so that
    downstream chunking in extractor.py can split on double-newlines correctly.
    """
    lines = [line.strip() for line in raw.splitlines()]
    # Collapse runs of empty lines into a single blank line (paragraph boundary)
    deduped: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = (line == "")
        if is_blank and prev_blank:
            continue
        deduped.append(line)
        prev_blank = is_blank
    return "\n".join(deduped)[:_MAX_TEXT_CHARS]


async def _scroll_to_bottom(page: Page):
    """Scroll the page incrementally to trigger lazy-loaded content."""
    await page.evaluate("""
        async () => {
            await new Promise((resolve) => {
                let total = 0;
                const step = 300;
                const timer = setInterval(() => {
                    window.scrollBy(0, step);
                    total += step;
                    if (total >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        }
    """)
    await page.wait_for_timeout(_SCROLL_DELAY_MS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def crawl_single(url: str) -> dict:
    """
    Scrape a single URL.

    Returns
    -------
    dict with keys:
        url          str   — original URL
        title        str   — <title> text
        text         str   — cleaned visible text (≤50 k chars)
        status       str   — "ok" | "error"
        error        str   — only present on error
        crawled_at   str   — ISO-8601 UTC timestamp
    """
    crawled_at = datetime.now(timezone.utc).isoformat()

    try:
        async with pool.acquire() as page:
            logger.info("Crawling %s", url)

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            http_status = response.status if response else None

            # Scroll to trigger lazy-loaded content (charts, price tickers, etc.)
            await _scroll_to_bottom(page)

            title = await page.title()
            raw_text = await page.inner_text("body")
            text = _clean_text(raw_text)

            char_count = len(text)
            logger.info("Crawled %s — %d chars (%d chunks est.)", url, char_count, max(1, char_count // 12_000))
            return {
                "url": url,
                "title": title,
                "text": text,
                "http_status": http_status,
                "char_count": char_count,
                "status": "ok",
                "cache_hit": False,
                "crawled_at": crawled_at,
            }

    except PlaywrightTimeout:
        logger.warning("Timeout crawling %s", url)
        return {
            "url": url,
            "status": "error",
            "error": f"Page load timed out after {_NAV_TIMEOUT_MS // 1000}s",
            "crawled_at": crawled_at,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error crawling %s", url)
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "crawled_at": crawled_at,
        }


async def crawl_parallel(urls: list[str], max_concurrency: int = 5) -> list[dict]:
    """
    Crawl multiple URLs concurrently, respecting max_concurrency.

    Uses a semaphore on top of the browser pool so that even if this function
    is called with 500 URLs, we never fire more than max_concurrency at once.
    """
    sem = asyncio.Semaphore(max_concurrency)

    async def _guarded(url: str) -> dict:
        async with sem:
            return await crawl_single(url)

    return await asyncio.gather(*(_guarded(u) for u in urls))
