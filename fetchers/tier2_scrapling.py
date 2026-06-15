"""
Tier 2: JS-rendered pages via Scrapling StealthyFetcher, with Playwright-lite fallback.
"""

from __future__ import annotations

import logging

from crawl_envelope import CrawlEnvelope
from fetchers.tier3_stealth_browser import fetch_tier3

logger = logging.getLogger(__name__)


async def fetch_tier2(url: str, envelope: CrawlEnvelope, retailer_config: dict | None = None) -> dict:
    try:
        from scrapling.fetchers import StealthyFetcher  # type: ignore

        max_chars = envelope.max_bytes or 200_000
        page = await StealthyFetcher.fetch(url, headless=True, network_idle=True)
        html = str(getattr(page, "html", "") or getattr(page, "text", "") or "")
        title = ""
        if hasattr(page, "title"):
            title = str(page.title or "")
        text = html[:max_chars]
        blocked = any(n in html[:8000].lower() for n in ("captcha", "access denied", "robot check"))
        if blocked:
            return {
                "url": url,
                "title": title,
                "text": text,
                "html": html[:max_chars],
                "http_status": 200,
                "status": "blocked",
                "block_reason": "captcha_required",
            }
        return {
            "url": url,
            "title": title,
            "text": text,
            "html": html[:max_chars],
            "http_status": 200,
            "char_count": len(text),
            "status": "ok",
        }
    except ImportError:
        logger.debug("Scrapling unavailable; Tier 2 delegating to Tier 3")
        return await fetch_tier3(url, envelope, retailer_config=retailer_config)
    except Exception as exc:
        logger.warning("Tier 2 fetch failed for %s: %s — falling back to Tier 3", url, exc)
        return await fetch_tier3(url, envelope, retailer_config=retailer_config)
