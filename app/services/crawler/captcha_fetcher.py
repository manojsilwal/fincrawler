"""Stealth browser fetch with CAPTCHA solving fallback."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.services.crawler.browser_fetcher import fetch_stealth_browser

logger = logging.getLogger(__name__)


def _extract_recaptcha_sitekey(html: str) -> str | None:
    for pattern in (
        r'data-sitekey=["\']([^"\']+)["\']',
        r'"sitekey"\s*:\s*"([^"]+)"',
        r'grecaptcha\.render\([^,]+,\s*\{\s*["\']sitekey["\']\s*:\s*["\']([^"\']+)',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            return m.group(1)
    return None


async def fetch_with_captcha_solve(url: str, retailer_key: str = "") -> dict:
    """
    Stealth browser fetch; on CAPTCHA block, attempt CapSolver/2Captcha and retry once.
    """
    result = await fetch_stealth_browser(url, retailer_key=retailer_key)
    if result.get("status") != "blocked":
        return result
    if result.get("block_reason") not in ("captcha", "cloudflare_challenge", None):
        return result

    from app.config import get_settings
    from app.services.asp.captcha import get_captcha_solvers, solve_recaptcha

    if not get_captcha_solvers():
        return result

    html = result.get("html") or ""
    site_key = _extract_recaptcha_sitekey(html)
    if not site_key:
        logger.info("CAPTCHA detected but no sitekey found for %s", url[:80])
        return result

    token, solver_name = await solve_recaptcha(site_key, result.get("url") or url)
    if not token:
        return result

    # Retry with solved token injected via Playwright
    from app.services.crawler.browser_pool import get_browser_pool
    from app.services.crawler.human_behavior import dismiss_consent, human_delay
    from app.services.crawler.retailer_profiles import get_retailer_profile

    settings = get_settings()
    profile = get_retailer_profile(retailer_key) if retailer_key else {}
    crawled_at = datetime.now(timezone.utc).isoformat()

    try:
        pool = await get_browser_pool(size=settings.browser_pool_size)
        async with pool.page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
            await human_delay(1000, 2000)
            await dismiss_consent(page, profile.get("consent_selectors", []))
            await page.evaluate(
                """(token) => {
                    const ta = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
                    if (ta) { ta.value = token; ta.innerHTML = token; }
                    if (typeof window.___grecaptcha_cfg !== 'undefined') {
                        try { document.querySelector('form')?.submit(); } catch(_) {}
                    }
                }""",
                token,
            )
            await page.wait_for_timeout(5000)
            page_text = await page.inner_text("body")
            title = await page.title()
            html2 = (await page.content())[:350_000]
            return {
                "url": page.url,
                "title": title,
                "text": page_text[:350_000],
                "page_text": page_text[:350_000],
                "html": html2,
                "http_status": 200,
                "char_count": len(page_text),
                "status": "ok",
                "tier_used": 3,
                "tier_name": "stealth_browser",
                "fetch_backend": "captcha_browser",
                "captcha_solver": solver_name,
                "crawled_at": crawled_at,
            }
    except Exception as exc:
        logger.exception("CAPTCHA retry failed for %s", url)
        result["captcha_retry_error"] = str(exc)
        return result
