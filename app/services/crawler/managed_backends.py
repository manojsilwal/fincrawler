"""Proxy httpx backend for ASP engine fallback."""

from __future__ import annotations

import re

import httpx

from app.config import get_settings
from app.services.compliance_checker import ComplianceChecker

_compliance = ComplianceChecker()


def pack_html_result(
    *,
    html: str,
    final_url: str,
    status: int | None,
    backend: str,
    crawled_at: str,
    tier_used: int = 4,
    page_text: str | None = None,
    title: str | None = None,
) -> dict:
    title_m = re.search(r"<title[^>]*>([^<]{1,500})</title>", html, re.I | re.S)
    parsed_title = title_m.group(1).strip() if title_m else ""
    title = title or parsed_title
    text = page_text if page_text is not None else re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()[:350_000]
    blocked, reason = _compliance.should_stop_after_response(text, status, tier_used=tier_used, url=final_url)
    base = {
        "url": final_url,
        "title": title,
        "text": text,
        "page_text": text,
        "html": html[:350_000],
        "http_status": status,
        "char_count": len(text),
        "tier_used": tier_used,
        "tier_name": "bank_grade",
        "fetch_backend": backend,
        "crawled_at": crawled_at,
    }
    if blocked:
        return {**base, "status": "blocked", "block_reason": reason}
    return {**base, "status": "ok"}


async def fetch_scrapfly(url: str, crawled_at: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
        r = await client.get(
            "https://api.scrapfly.io/scrape",
            params={
                "key": settings.scrapfly_api_key.strip(),
                "url": url,
                "asp": "true",
                "render_js": "true",
            },
        )
        r.raise_for_status()
        payload = r.json()
        html = payload.get("result", {}).get("content") or payload.get("result", {}).get("html") or ""
        status = payload.get("result", {}).get("status_code") or 200
        final_url = payload.get("result", {}).get("url") or url
    return pack_html_result(html=html, final_url=final_url, status=status, backend="scrapfly", crawled_at=crawled_at)


async def fetch_proxy_http(url: str, crawled_at: str, proxy: str | None = None) -> dict:
    settings = get_settings()
    proxy = proxy or settings.managed_proxy_url or None
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(90.0),
            proxy=proxy,
            headers={"User-Agent": settings.crawler_user_agent},
        ) as client:
            r = await client.get(url)
            html = r.text
            status = r.status_code
            final_url = str(r.url)
        return pack_html_result(html=html, final_url=final_url, status=status, backend="proxy_http", crawled_at=crawled_at)
    except Exception as exc:
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "tier_used": 4,
            "tier_name": "bank_grade",
            "fetch_backend": "proxy_http",
            "crawled_at": crawled_at,
        }
