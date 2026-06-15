"""
Tier 4: Managed scraping API (Scrapfly) or proxy-backed httpx as fallback.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

import httpx

from crawl_envelope import CrawlEnvelope

logger = logging.getLogger(__name__)


def _clean_text(raw: str, max_chars: int = 200_000) -> str:
    return raw[:max_chars]


async def fetch_tier4(url: str, envelope: CrawlEnvelope) -> dict:
    crawled_at = datetime.now(timezone.utc).isoformat()
    max_chars = envelope.max_bytes or 350_000
    api_key = os.getenv("SCRAPFLY_API_KEY", "").strip()

    try:
        if api_key:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
                r = await client.get(
                    "https://api.scrapfly.io/scrape",
                    params={
                        "key": api_key,
                        "url": url,
                        "asp": "true",
                        "render_js": "true",
                    },
                )
                r.raise_for_status()
                payload = r.json()
                html = (
                    payload.get("result", {}).get("content")
                    or payload.get("result", {}).get("html")
                    or ""
                )
                status = payload.get("result", {}).get("status_code") or 200
        else:
            proxy = envelope.proxy.get("url") if envelope.proxy else os.getenv("MANAGED_PROXY_URL")
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(60.0),
                proxy=proxy,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    )
                },
            ) as client:
                r = await client.get(url)
                html = r.text
                status = r.status_code

        title_m = re.search(r"<title[^>]*>([^<]{1,500})</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_m.group(1).strip() if title_m else ""
        text = _clean_text(re.sub(r"<[^>]+>", " ", html), max_chars)
        blocked = status in (403, 503) or "captcha" in html[:5000].lower()
        if blocked:
            return {
                "url": url,
                "title": title,
                "text": text,
                "html": html[:max_chars],
                "http_status": status,
                "status": "blocked",
                "block_reason": "ip_blocked" if status in (403, 503) else "captcha_required",
                "crawled_at": crawled_at,
            }
        return {
            "url": url,
            "title": title,
            "text": text,
            "html": html[:max_chars],
            "http_status": status,
            "char_count": len(text),
            "status": "ok",
            "crawled_at": crawled_at,
        }
    except Exception as exc:
        logger.warning("Tier 4 fetch failed for %s: %s", url, exc)
        return {"url": url, "status": "error", "error": str(exc), "crawled_at": crawled_at}
