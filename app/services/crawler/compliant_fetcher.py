"""Tier 1: honest httpx fetch."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from app.config import get_settings


def _clean_text(html: str, max_chars: int = 200_000) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return text[:max_chars]


async def fetch_compliant(url: str) -> dict:
    settings = get_settings()
    crawled_at = datetime.now(timezone.utc).isoformat()
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(settings.fetch_timeout_seconds),
            headers=headers,
        ) as client:
            r = await client.get(url)
            html = r.text
            final_url = str(r.url)
            title_m = re.search(r"<title[^>]*>([^<]{1,500})</title>", html, re.I | re.S)
            title = title_m.group(1).strip() if title_m else ""
            text = _clean_text(html)
            return {
                "url": final_url,
                "title": title,
                "text": text,
                "page_text": text,
                "html": html[:350_000],
                "http_status": r.status_code,
                "char_count": len(text),
                "status": "ok",
                "tier_used": 1,
                "tier_name": "compliant",
                "crawled_at": crawled_at,
            }
    except Exception as exc:
        return {"url": url, "status": "error", "error": str(exc), "tier_used": 1, "crawled_at": crawled_at}
