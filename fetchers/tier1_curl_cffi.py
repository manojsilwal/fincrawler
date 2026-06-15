"""
Tier 1: static HTML / API fetch with TLS impersonation (curl_cffi) or httpx fallback.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from crawl_envelope import CrawlEnvelope

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _clean_text(raw: str, max_chars: int = 200_000) -> str:
    lines = [line.strip() for line in raw.splitlines()]
    deduped: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line == ""
        if blank and prev_blank:
            continue
        deduped.append(line)
        prev_blank = blank
    return "\n".join(deduped)[:max_chars]


def _title_from_html(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]{1,500})</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


async def fetch_tier1(url: str, envelope: CrawlEnvelope) -> dict:
    crawled_at = datetime.now(timezone.utc).isoformat()
    max_chars = envelope.max_bytes or 200_000
    proxy = None
    if envelope.proxy and envelope.proxy.get("url"):
        proxy = envelope.proxy["url"]

    try:
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore

            async with AsyncSession(impersonate="chrome120") as session:
                r = await session.get(url, timeout=30, proxy=proxy)
                html = r.text
                status = r.status_code
        except ImportError:
            logger.debug("curl_cffi unavailable; Tier 1 using httpx")
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": _BROWSER_UA},
                proxy=proxy,
            ) as client:
                r = await client.get(url)
                html = r.text
                status = r.status_code

        text = _clean_text(re.sub(r"<[^>]+>", " ", html), max_chars)
        title = _title_from_html(html)
        blocked = status in (403, 503) or any(
            n in html[:8000].lower()
            for n in ("captcha", "access denied", "robot check", "unusual traffic")
        )
        if blocked:
            return {
                "url": url,
                "title": title,
                "text": text,
                "html": html[:max_chars],
                "http_status": status,
                "status": "blocked",
                "block_reason": "access_denied" if status in (403, 503) else "captcha_required",
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
        logger.warning("Tier 1 fetch failed for %s: %s", url, exc)
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "crawled_at": crawled_at,
        }
