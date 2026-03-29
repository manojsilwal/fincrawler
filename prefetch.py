# prefetch.py
"""
Background scheduler that pre-warms the cache with hot S&P 500 ticker URLs.
Runs every 5 minutes using APScheduler so that most finance-agent requests
hit the cache (< 1 ms) instead of waiting for a live crawl (5-10 s).
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from cache import cache
from crawler import crawl_parallel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker list — extend to the full S&P 500 as needed
# ---------------------------------------------------------------------------
SP500_TICKERS = [
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN",
    "META", "TSLA", "BRK-B", "JPM", "V",
    "UNH", "XOM", "LLY", "JNJ", "MA",
    "PG", "HD", "AVGO", "MRK", "CVX",
    "ABBV", "COST", "PEP", "ADBE", "KO",
    "WMT", "MCD", "CSCO", "BAC", "CRM",
    "TMO", "ACN", "NFLX", "ABT", "LIN",
    "AMD", "DHR", "INTC", "NEE", "PM",
    "WFC", "TXN", "INTU", "UPS", "RTX",
    "LOW", "AMGN", "SPGI", "CAT", "BKNG",
]

# URL templates to pre-fetch per ticker
PREFETCH_TEMPLATES = [
    "https://finance.yahoo.com/quote/{ticker}",
    "https://finance.yahoo.com/quote/{ticker}/news",
]


async def prefetch_hot_tickers(tickers: list[str] | None = None):
    """
    Crawl the top N tickers and warm the cache.
    Called by APScheduler every 5 minutes.
    """
    tickers = tickers or SP500_TICKERS
    urls = [
        template.format(ticker=ticker)
        for ticker in tickers[:50]
        for template in PREFETCH_TEMPLATES
    ]

    logger.info("Pre-fetching %d finance URLs for %d tickers…", len(urls), min(len(tickers), 50))
    results = await crawl_parallel(urls, max_concurrency=5)

    saved = 0
    errors = 0
    for result in results:
        if result.get("status") == "ok":
            await cache.set(result["url"], result)
            saved += 1
        else:
            errors += 1
            logger.warning("Pre-fetch failed for %s: %s", result.get("url"), result.get("error"))

    logger.info("Pre-fetch complete: %d cached, %d errors (total=%d)", saved, errors, len(results))


def start_scheduler() -> AsyncIOScheduler:
    """Create and start the background APScheduler. Returns the scheduler instance."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        prefetch_hot_tickers,
        trigger="interval",
        minutes=5,
        id="prefetch_job",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info("Pre-fetch scheduler started (every 5 min).")
    return scheduler
