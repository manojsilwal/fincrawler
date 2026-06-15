"""Tier 2: TLS/browser fingerprint impersonation via curl_cffi."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.config import get_settings
from app.services.compliance_checker import ComplianceChecker

_compliance = ComplianceChecker()


async def fetch_curl(url: str) -> dict:
    crawled_at = datetime.now(timezone.utc).isoformat()
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return {
            "url": url,
            "status": "error",
            "error": "curl_cffi_not_installed",
            "tier_used": 2,
            "tier_name": "tls_impersonate",
            "crawled_at": crawled_at,
        }

    settings = get_settings()
    proxy = settings.managed_proxy_url or None
    try:
        async with AsyncSession() as session:
            r = await session.get(
                url,
                impersonate="chrome124",
                timeout=settings.fetch_timeout_seconds,
                proxy=proxy,
                allow_redirects=True,
            )
            html = r.text
            final_url = str(r.url)
            status = r.status_code
        title_m = re.search(r"<title[^>]*>([^<]{1,500})</title>", html, re.I | re.S)
        title = title_m.group(1).strip() if title_m else ""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()[:350_000]
        blocked, reason = _compliance.should_stop_after_response(text, status, tier_used=2, url=final_url)
        if blocked:
            return {
                "url": final_url,
                "title": title,
                "text": text,
                "page_text": text,
                "html": html[:350_000],
                "http_status": status,
                "char_count": len(text),
                "status": "blocked",
                "block_reason": reason,
                "tier_used": 2,
                "tier_name": "tls_impersonate",
                "fetch_backend": "curl_cffi",
                "crawled_at": crawled_at,
            }
        return {
            "url": final_url,
            "title": title,
            "text": text,
            "page_text": text,
            "html": html[:350_000],
            "http_status": status,
            "char_count": len(text),
            "status": "ok",
            "tier_used": 2,
            "tier_name": "tls_impersonate",
            "fetch_backend": "curl_cffi",
            "crawled_at": crawled_at,
        }
    except Exception as exc:
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "tier_used": 2,
            "tier_name": "tls_impersonate",
            "fetch_backend": "curl_cffi",
            "crawled_at": crawled_at,
        }
