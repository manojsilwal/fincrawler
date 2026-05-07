# browser_pool.py
"""
Async Playwright browser pool.
Manages a fixed number of persistent browser contexts so that concurrent
scrape requests share pre-warmed browsers rather than spawning one per request.

Stealth configuration is centralised in stealth.py.
shop_crawler.py creates its own contexts directly on pool._browser for
per-request UA/viewport randomisation.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from stealth import STEALTH_LAUNCH_ARGS, apply_stealth, get_stealth_context_kwargs

logger = logging.getLogger(__name__)


class BrowserPool:
    """
    A simple semaphore-backed pool of Playwright browser contexts.

    Usage
    -----
    pool = BrowserPool(size=3)
    await pool.initialize()

    async with pool.acquire() as page:
        await page.goto("https://example.com")
        ...

    await pool.teardown()
    """

    def __init__(self, size: int = 3):
        self._size = size
        self._playwright = None
        self._browser: Browser | None = None
        self._semaphore: asyncio.Semaphore | None = None

    async def initialize(self):
        """Launch the underlying Chromium browser with stealth flags."""
        logger.info("Initialising Playwright browser pool (size=%d)…", self._size)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(**STEALTH_LAUNCH_ARGS)
        self._semaphore = asyncio.Semaphore(self._size)
        logger.info("Browser pool ready (stealth mode enabled).")

    async def teardown(self):
        """Close all browser resources gracefully."""
        logger.info("Tearing down browser pool…")
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser pool torn down.")

    @asynccontextmanager
    async def acquire(self):
        """
        Async context manager that yields an isolated Page.
        Blocks when all pool slots are busy, then releases on exit.
        """
        if self._browser is None or self._semaphore is None:
            raise RuntimeError("BrowserPool is not initialised. Call initialize() first.")

        async with self._semaphore:
            context: BrowserContext = await self._browser.new_context(
                **get_stealth_context_kwargs()
            )
            page: Page = await context.new_page()
            # Inject stealth JS before any navigation
            await apply_stealth(page)
            try:
                yield page
            finally:
                await context.close()


# Module-level singleton — imported by main.py and crawler.py
pool = BrowserPool(size=3)
