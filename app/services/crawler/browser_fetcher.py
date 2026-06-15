"""Tier 3: stealth Playwright with session warming, proxy rotation, and challenge handling."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.config import get_settings
from app.services.compliance_checker import ComplianceChecker
from app.services.crawler.browser_pool import get_browser_pool
from app.services.crawler.human_behavior import dismiss_consent, human_delay, run_behavior
from app.services.crawler.retailer_profiles import get_retailer_profile
from app.services.crawler.session_store import is_warmed, mark_warmed

logger = logging.getLogger(__name__)
_compliance = ComplianceChecker()

_BLOCK_REASONS_ROTATE = frozenset(
    {"captcha", "access_denied", "blocked", "cloudflare_challenge", "no_product_list", "rate_limited"}
)


async def _detect_block(page) -> str | None:
    title = (await page.title()).lower()
    url = page.url.lower()
    body = ""
    try:
        body = (await page.inner_text("body"))[:2000].lower()
    except Exception:
        pass
    if "robot or human" in body or "robot" in title:
        return "captcha"
    if "captcha" in body or "verify you are human" in body:
        return "captcha"
    if "/blocked" in url or "walmart.com/blocked" in url:
        return "blocked"
    if "access denied" in title or "access denied" in body:
        return "access_denied"
    if "cloudflare" in body or "just a moment" in title:
        return "cloudflare_challenge"
    if "error page" in title and "ebay" in url:
        return "access_denied"
    if "sorry! something went wrong" in title:
        return "access_denied"
    return None


def _has_product_markers(html: str, profile: dict) -> bool:
    blob = html[:120_000].lower()
    markers = profile.get("product_markers") or []
    return any(m.lower() in blob for m in markers)


def _clean_body_text(raw: str, max_chars: int = 120_000) -> str:
    lines = raw.splitlines()
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        s = line.strip()
        blank = s == ""
        if blank and prev_blank:
            continue
        cleaned.append(s)
        prev_blank = blank
    return "\n".join(cleaned)[:max_chars]


async def _handle_challenge(page, profile: dict, wait_ms: int) -> str | None:
    reason = await _detect_block(page)
    if reason not in ("cloudflare_challenge", "captcha"):
        return reason
    logger.warning("Challenge detected (%s), waiting %dms", reason, wait_ms)
    await page.wait_for_timeout(wait_ms)
    await dismiss_consent(page, profile.get("consent_selectors", []))
    reason = await _detect_block(page)
    if reason in ("cloudflare_challenge", "captcha"):
        try:
            await page.reload(wait_until="domcontentloaded", timeout=25_000)
            await page.wait_for_timeout(6_000)
            await dismiss_consent(page, profile.get("consent_selectors", []))
            reason = await _detect_block(page)
        except Exception:
            pass
    return reason


async def _fetch_stealth_browser_once(
    url: str,
    *,
    retailer_key: str = "",
    proxy_url: str | None = None,
    fetch_backend: str = "asp_js_browser",
) -> dict:
    from app.services.asp.antibot.cookie_store import (
        egress_id_from_proxy,
        load_storage_state,
        save_storage_state,
    )

    settings = get_settings()
    crawled_at = datetime.now(timezone.utc).isoformat()
    profile = get_retailer_profile(retailer_key) if retailer_key else {}
    timeout_ms = settings.browser_nav_timeout_ms
    egress_id = egress_id_from_proxy(proxy_url)
    storage_state = await load_storage_state(retailer_key, egress_id) if retailer_key else None

    try:
        pool = await get_browser_pool(size=settings.browser_pool_size)
        async with pool.page(
            proxy_url=proxy_url,
            retailer_key=retailer_key,
            storage_state=storage_state,
        ) as (page, context):
            warm = profile.get("warm_session", True)
            homepage = profile.get("homepage_url")
            if warm and homepage and retailer_key and not is_warmed(retailer_key):
                logger.info("[%s] warming session via %s", retailer_key, homepage)
                await page.goto(homepage, wait_until="domcontentloaded", timeout=timeout_ms)
                await human_delay(1000, 2000)
                await dismiss_consent(page, profile.get("consent_selectors", []))
                mark_warmed(retailer_key)

            proxy_note = f" via {proxy_url[:40]}..." if proxy_url and len(proxy_url) > 40 else (
                f" via {proxy_url}" if proxy_url else ""
            )
            logger.info("Stealth browser fetch → %s%s", url, proxy_note)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            http_status = resp.status if resp else None
            await human_delay(1200, 2500)
            await dismiss_consent(page, profile.get("consent_selectors", []))

            block_reason = await _handle_challenge(page, profile, settings.challenge_wait_ms)

            # In-house antibot solver (PerimeterX / DataDome) when passive wait fails
            if block_reason in ("captcha", "blocked", "cloudflare_challenge") and settings.enable_antibot_solver:
                from app.services.asp.antibot import solve_challenge

                html_probe = await page.content()
                vendor_hint = profile.get("antibot")
                solved = await solve_challenge(
                    page,
                    vendor=vendor_hint,
                    html=html_probe,
                    url=page.url,
                )
                if solved:
                    block_reason = await _detect_block(page)
                else:
                    block_reason = block_reason or await _detect_block(page)

            hydration_wait = profile.get("hydration_wait_ms")
            if hydration_wait and not block_reason:
                await page.wait_for_timeout(int(hydration_wait))

            wait_sel = profile.get("wait_selector")
            if wait_sel and not block_reason:
                try:
                    await page.wait_for_selector(wait_sel, timeout=12_000, state="visible")
                except Exception:
                    pass

            await run_behavior(page)

            # Heavily lazy-loaded SPAs (e.g. Target) may need a second pass to hydrate
            # product cards before we can read them.
            if profile.get("extra_scroll") and not block_reason:
                html_probe = await page.content()
                if not _has_product_markers(html_probe, profile):
                    await run_behavior(page)
                    if wait_sel:
                        try:
                            await page.wait_for_selector(wait_sel, timeout=10_000, state="visible")
                        except Exception:
                            pass

            block_reason = block_reason or await _detect_block(page)

            page_text = _clean_body_text(await page.inner_text("body"))
            title = await page.title()
            html = (await page.content())[:350_000]
            final_url = page.url

            base = {
                "url": final_url,
                "title": title,
                "text": page_text,
                "page_text": page_text,
                "html": html,
                "http_status": http_status,
                "char_count": len(page_text),
                "tier_used": 3,
                "tier_name": "stealth_browser",
                "fetch_backend": fetch_backend,
                "crawled_at": crawled_at,
            }
            if proxy_url:
                base["proxy_url"] = proxy_url

            if block_reason:
                return {**base, "status": "blocked", "block_reason": block_reason}

            if profile and not _has_product_markers(html, profile) and len(page_text) < 3000:
                return {**base, "status": "blocked", "block_reason": "no_product_list"}

            blocked, reason = _compliance.should_stop_after_response(
                page_text, http_status, tier_used=3, url=final_url
            )
            if blocked:
                return {**base, "status": "blocked", "block_reason": reason}

            if retailer_key:
                try:
                    state = await context.storage_state()
                    await save_storage_state(retailer_key, state, egress_id)
                except Exception:
                    logger.debug("Failed to persist antibot cookies for %s", retailer_key, exc_info=True)

            return {**base, "status": "ok"}
    except PlaywrightTimeout:
        return {
            "url": url,
            "status": "error",
            "error": "browser_timeout",
            "tier_used": 3,
            "tier_name": "stealth_browser",
            "fetch_backend": fetch_backend,
            "crawled_at": crawled_at,
            "proxy_url": proxy_url,
        }
    except Exception as exc:
        logger.exception("Stealth browser fetch failed for %s", url)
        return {
            "url": url,
            "status": "error",
            "error": str(exc),
            "tier_used": 3,
            "tier_name": "stealth_browser",
            "fetch_backend": fetch_backend,
            "crawled_at": crawled_at,
            "proxy_url": proxy_url,
        }


async def fetch_stealth_browser(
    url: str,
    retailer_key: str = "",
    *,
    proxy_url: str | None = None,
    fetch_backend: str = "asp_js_browser",
) -> dict:
    """Fetch with optional proxy; rotates internal egress pool on retailer blocks."""
    from app.services.asp.proxy_pool import (
        clear_sticky_proxy,
        get_next_proxy,
        mark_proxy_failure,
        mark_proxy_success,
    )
    from app.services.asp.proxy_utils import is_direct_egress

    settings = get_settings()
    use_pool = settings.browser_proxy_enabled and proxy_url is None
    max_attempts = settings.browser_proxy_max_retries if use_pool else 1
    last: dict = {"url": url, "status": "error", "error": "no_attempt"}
    used_real_proxy = False

    for attempt in range(max_attempts):
        pick = proxy_url
        if use_pool:
            pool_pick = get_next_proxy(retailer_key=retailer_key)
            if pool_pick:
                pick = pool_pick
                if is_direct_egress(pick):
                    pick = None
            elif attempt > 0:
                break
        if pick:
            used_real_proxy = True

        last = await _fetch_stealth_browser_once(
            url,
            retailer_key=retailer_key,
            proxy_url=pick,
            fetch_backend=fetch_backend,
        )

        if last.get("status") == "ok":
            if pick:
                mark_proxy_success(pick, retailer_key=retailer_key)
            return last

        block_reason = last.get("block_reason") or ""
        if use_pool and pick and block_reason in _BLOCK_REASONS_ROTATE:
            mark_proxy_failure(pick, retailer_key=retailer_key)
            clear_sticky_proxy(retailer_key)
            logger.info(
                "[%s] proxy block (%s) — rotating egress (%d/%d)",
                retailer_key,
                block_reason,
                attempt + 1,
                max_attempts,
            )
            continue

        if last.get("status") == "error" and use_pool and pick:
            mark_proxy_failure(pick, retailer_key=retailer_key)
            clear_sticky_proxy(retailer_key)
            continue

        break

    # If every proxied attempt failed, try once directly so a broken egress
    # node can never take down all fetches.
    if used_real_proxy and last.get("status") != "ok":
        logger.info("[%s] proxy attempts exhausted — retrying direct egress", retailer_key)
        direct = await _fetch_stealth_browser_once(
            url,
            retailer_key=retailer_key,
            proxy_url=None,
            fetch_backend=fetch_backend,
        )
        if direct.get("status") == "ok":
            return direct
        last = direct

    return last


# Back-compat alias used by managed_fetcher auto mode
async def fetch_browser(url: str, retailer_key: str = "") -> dict:
    return await fetch_stealth_browser(url, retailer_key=retailer_key)
