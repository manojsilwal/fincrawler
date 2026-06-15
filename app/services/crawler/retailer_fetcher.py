"""Backward-compatible retailer fetch — use ASP engine."""

from __future__ import annotations

from app.services.asp import asp_engine


async def fetch_retailer(url: str, retailer_key: str = "") -> dict:
    return await asp_engine.scrape_url(url, retailer_key=retailer_key)
