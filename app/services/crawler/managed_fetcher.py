"""Tier 4 entry — delegates to internal FinCrawler ASP engine."""

from __future__ import annotations

from app.services.asp import ScrapeOptions, asp_engine


async def fetch_managed(url: str, retailer_key: str = "") -> dict:
    return await asp_engine.scrape_url(
        url,
        asp=True,
        render_js=True,
        retailer_key=retailer_key,
        retry_on_block=True,
    )
