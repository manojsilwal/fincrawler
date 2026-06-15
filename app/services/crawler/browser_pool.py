"""Lightweight Playwright browser pool with stealth launch args."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import get_settings
from app.services.crawler.stealth import STEALTH_LAUNCH_ARGS, apply_stealth, get_stealth_context_kwargs

logger = logging.getLogger(__name__)


class BrowserPool:
    def __init__(self, size: int = 2):
        self._size = max(1, size)
        self._playwright = None
        self._browser: Browser | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        settings = get_settings()
        launch = dict(STEALTH_LAUNCH_ARGS)
        launch["headless"] = settings.browser_headless
        logger.info("Starting stealth Playwright pool (size=%d, headless=%s)", self._size, launch["headless"])
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(**launch)
        self._semaphore = asyncio.Semaphore(self._size)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        logger.info("Stopping Playwright browser pool")
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._semaphore = None
        self._started = False

    @asynccontextmanager
    async def page(self, *, proxy_url: str | None = None, retailer_key: str = ""):
        if not self._started or self._browser is None or self._semaphore is None:
            raise RuntimeError("BrowserPool not started")
        async with self._semaphore:
            from app.services.asp.proxy_utils import playwright_proxy_kwargs

            kwargs = get_stealth_context_kwargs(retailer_key)
            pw_proxy = playwright_proxy_kwargs(proxy_url)
            if pw_proxy:
                kwargs["proxy"] = pw_proxy
            context: BrowserContext = await self._browser.new_context(**kwargs)
            page: Page = await context.new_page()
            await apply_stealth(page, retailer_key)
            try:
                yield page
            finally:
                await context.close()


_pool: BrowserPool | None = None
_pool_lock = asyncio.Lock()


async def get_browser_pool(size: int = 2) -> BrowserPool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = BrowserPool(size=size)
            await _pool.start()
        elif not _pool._started:
            await _pool.start()
    return _pool


async def shutdown_browser_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.stop()
        _pool = None
