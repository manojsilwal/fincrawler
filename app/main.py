"""FinCrawler hybrid compliant shopping intelligence API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import asp, crawl_jobs, finance_compat, health, products, rankings, shop, sources, zenith_compat
from firecrawl_compat import router as firecrawl_router
from app.config import get_settings
from app.database import init_db

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from scripts.seed_sources import main as seed_sources

        seed_sources()
    except Exception:
        logging.getLogger(__name__).exception("source seed skipped")

    settings = get_settings()
    if settings.enable_browser_tier4 and settings.managed_fetcher_mode in ("auto", "browser"):
        try:
            from app.services.asp.provider_health import reset_provider
            from app.services.crawler.browser_pool import get_browser_pool

            reset_provider("browser_grid")
            reset_provider("js_browser")
            await get_browser_pool(size=settings.browser_pool_size)
        except Exception:
            logging.getLogger(__name__).exception("browser pool warmup skipped")

    yield

    if settings.enable_browser_tier4:
        try:
            from app.services.crawler.browser_pool import shutdown_browser_pool

            await shutdown_browser_pool()
        except Exception:
            logging.getLogger(__name__).exception("browser pool shutdown failed")


app = FastAPI(
    title="FinCrawler Shopping Intel",
    description="Hybrid compliant shopping intelligence with Tier-4 managed escalation",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(asp.router)
app.include_router(sources.router)
app.include_router(crawl_jobs.router)
app.include_router(products.router)
app.include_router(rankings.router)
app.include_router(shop.router)
app.include_router(zenith_compat.router)
app.include_router(finance_compat.router)
app.include_router(firecrawl_router)
