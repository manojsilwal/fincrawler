"""Stealth browser fetch with in-house antibot + optional paid CAPTCHA fallback."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.config import get_settings
from app.services.asp.antibot.cookie_store import (
    egress_id_from_proxy,
    load_storage_state,
    save_storage_state,
)
from app.services.asp.antibot.detector import detect_antibot_vendor
from app.services.asp.antibot import solve_challenge
from app.services.crawler.browser_fetcher import _clean_body_text, _detect_block
from app.services.crawler.human_behavior import dismiss_consent, human_delay, run_behavior
from app.services.crawler.retailer_profiles import get_retailer_profile

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


async def _fetch_page_result(page, context, *, crawled_at: str, solver_name: str | None = None) -> dict:
    page_text = _clean_body_text(await page.inner_text("body"))
    title = await page.title()
    html = (await page.content())[:350_000]
    result = {
        "url": page.url,
        "title": title,
        "text": page_text,
        "page_text": page_text,
        "html": html,
        "http_status": 200,
        "char_count": len(page_text),
        "status": "ok",
        "tier_used": 3,
        "tier_name": "stealth_browser",
        "fetch_backend": "captcha_browser",
        "crawled_at": crawled_at,
    }
    if solver_name:
        result["captcha_solver"] = solver_name
    return result


async def fetch_with_captcha_solve(
    url: str,
    retailer_key: str = "",
    *,
    proxy_url: str | None = None,
) -> dict:
    """
    Open a live browser session, navigate, solve antibot challenges in-house,
    then fall back to CapSolver/2Captcha for reCAPTCHA if configured.
    """
    settings = get_settings()
    profile = get_retailer_profile(retailer_key) if retailer_key else {}
    crawled_at = datetime.now(timezone.utc).isoformat()
    egress_id = egress_id_from_proxy(proxy_url)
    storage_state = await load_storage_state(retailer_key, egress_id) if retailer_key else None

    from app.services.crawler.browser_pool import get_browser_pool

    try:
        pool = await get_browser_pool(size=settings.browser_pool_size)
        async with pool.page(
            proxy_url=proxy_url,
            retailer_key=retailer_key,
            storage_state=storage_state,
        ) as (page, context):
            warm = profile.get("warm_session", True)
            homepage = profile.get("homepage_url")
            if warm and homepage and retailer_key:
                await page.goto(homepage, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
                await human_delay(800, 1500)
                await dismiss_consent(page, profile.get("consent_selectors", []))

            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
            await human_delay(1000, 2000)
            await dismiss_consent(page, profile.get("consent_selectors", []))

            html = await page.content()
            vendor = profile.get("antibot") or detect_antibot_vendor(html=html, url=page.url)
            block = await _detect_block(page)

            if block or vendor:
                if settings.enable_antibot_solver and vendor in ("perimeterx", "datadome"):
                    solved = await solve_challenge(page, vendor=vendor, html=html, url=page.url)
                    if solved:
                        await run_behavior(page)
                        if retailer_key:
                            state = await context.storage_state()
                            await save_storage_state(retailer_key, state, egress_id)
                        return await _fetch_page_result(page, context, crawled_at=crawled_at, solver_name=f"antibot_{vendor}")

                # Paid reCAPTCHA fallback
                from app.services.asp.captcha import get_captcha_solvers, solve_recaptcha

                if get_captcha_solvers() and (vendor == "recaptcha" or block in ("captcha", "cloudflare_challenge")):
                    site_key = _extract_recaptcha_sitekey(html)
                    if site_key:
                        token, solver_name = await solve_recaptcha(site_key, page.url)
                        if token:
                            await page.evaluate(
                                """(token) => {
                                    const ta = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
                                    if (ta) { ta.value = token; ta.innerHTML = token; }
                                    try { document.querySelector('form')?.submit(); } catch(_) {}
                                }""",
                                token,
                            )
                            await page.wait_for_timeout(5000)
                            if not await _detect_block(page):
                                if retailer_key:
                                    state = await context.storage_state()
                                    await save_storage_state(retailer_key, state, egress_id)
                                return await _fetch_page_result(
                                    page, context, crawled_at=crawled_at, solver_name=solver_name
                                )

            if await _detect_block(page):
                return {
                    "url": page.url,
                    "status": "blocked",
                    "block_reason": block or vendor or "captcha",
                    "html": html[:50_000],
                    "fetch_backend": "captcha_browser",
                    "crawled_at": crawled_at,
                }

            await run_behavior(page)
            if retailer_key:
                state = await context.storage_state()
                await save_storage_state(retailer_key, state, egress_id)
            return await _fetch_page_result(page, context, crawled_at=crawled_at)

    except Exception as exc:
        logger.exception("CAPTCHA/antibot fetch failed for %s", url)
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "fetch_backend": "captcha_browser",
            "crawled_at": crawled_at,
        }
