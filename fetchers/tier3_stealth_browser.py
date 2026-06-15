"""
Tier 3: Playwright stealth browser (existing FinCrawler engine).
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeout

from behavior.human_sim import dismiss_consent, human_delay, run_behavior
from browser_pool import pool
from crawl_envelope import CrawlEnvelope
from session.store import is_warmed, mark_warmed
from stealth import apply_stealth, get_stealth_context_kwargs

logger = logging.getLogger(__name__)


async def _detect_block(page) -> str | None:
    title = (await page.title()).lower()
    url = page.url.lower()
    body = ""
    try:
        body = (await page.inner_text("body"))[:1000].lower()
    except Exception:
        pass
    if "robot" in body or "captcha" in body:
        return "captcha"
    if "access denied" in title or "access denied" in body:
        return "access_denied"
    if "cloudflare" in body or "just a moment" in title:
        return "cloudflare_challenge"
    if "blocked" in title or "blocked" in body[:200]:
        return "blocked"
    if "sign in" in title and "search" not in url:
        return "login_wall"
    return None


async def fetch_tier3(
    url: str,
    envelope: CrawlEnvelope,
    retailer_config: dict | None = None,
) -> dict:
    crawled_at = datetime.now(timezone.utc).isoformat()
    cfg = retailer_config or {}
    retailer_key = envelope.retailer_key or cfg.get("retailer_key", "")

    if pool._browser is None:
        return {"url": url, "status": "error", "error": "browser_pool_not_ready", "crawled_at": crawled_at}

    try:
        context_kwargs = get_stealth_context_kwargs()
        context = await pool._browser.new_context(**context_kwargs)
        page = await context.new_page()
        try:
            await apply_stealth(page)
            warm = envelope.warm_session if envelope.warm_session is not None else cfg.get("warm_session", True)
            homepage = cfg.get("homepage_url")
            if warm and homepage and retailer_key and not is_warmed(envelope.session_id, retailer_key):
                logger.info("[%s] Warming session via %s", retailer_key, homepage)
                await page.goto(homepage, wait_until="domcontentloaded", timeout=20_000)
                await human_delay(1000, 2000)
                await dismiss_consent(page, cfg.get("consent_selectors", []))
                mark_warmed(envelope.session_id, retailer_key)

            logger.info("Tier3 crawl → %s", url)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
            http_status = resp.status if resp else None
            await human_delay(1200, 2500)
            await dismiss_consent(page, cfg.get("consent_selectors", []))

            block_reason = await _detect_block(page)
            if block_reason in ("cloudflare_challenge", "captcha"):
                logger.warning("Challenge detected (%s), waiting 8s", block_reason)
                await page.wait_for_timeout(8_000)
                block_reason = await _detect_block(page)

            wait_sel = cfg.get("wait_selector")
            if wait_sel:
                try:
                    await page.wait_for_selector(wait_sel, timeout=12_000, state="visible")
                except Exception:
                    pass

            await run_behavior(page, envelope.behavior)
            final_block = await _detect_block(page)
            if final_block in ("captcha", "access_denied", "blocked"):
                return {
                    "url": url,
                    "status": "blocked",
                    "block_reason": final_block,
                    "http_status": http_status,
                    "crawled_at": crawled_at,
                }

            raw_text = await page.inner_text("body")
            lines = raw_text.splitlines()
            cleaned: list[str] = []
            prev_blank = False
            for line in lines:
                s = line.strip()
                blank = s == ""
                if blank and prev_blank:
                    continue
                cleaned.append(s)
                prev_blank = blank
            page_text = "\n".join(cleaned)[:80_000]
            title = await page.title()
            html = await page.content()

            return {
                "url": page.url,
                "title": title,
                "text": page_text,
                "page_text": page_text,
                "html": html[: envelope.max_bytes or 350_000],
                "char_count": len(page_text),
                "http_status": http_status,
                "status": "ok",
                "crawled_at": crawled_at,
            }
        finally:
            await context.close()
    except PlaywrightTimeout:
        return {"url": url, "status": "error", "error": "timeout", "crawled_at": crawled_at}
    except Exception as exc:
        logger.exception("Tier 3 fetch failed for %s", url)
        return {"url": url, "status": "error", "error": str(exc), "crawled_at": crawled_at}


async def fetch_retailer_search(
    retailer_key: str,
    query: str,
    envelope: CrawlEnvelope,
    retailer_config: dict[str, Any],
) -> dict:
    encoded = urllib.parse.quote_plus(query)
    url = retailer_config["search_url"].format(query=encoded)
    envelope.retailer_key = retailer_key
    result = await fetch_tier3(url, envelope, retailer_config=retailer_config)
    result["retailer"] = retailer_config.get("name", retailer_key)
    result["retailer_key"] = retailer_key
    result["query"] = query
    return result
