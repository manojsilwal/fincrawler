"""PerimeterX press-and-hold challenge solver."""

from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger(__name__)

_PX_IFRAME_SELECTORS = (
    "#px-captcha",
    "iframe[src*='captcha']",
    "iframe[src*='px-captcha']",
    "iframe[title*='Human']",
)


async def _is_still_blocked(page) -> bool:
    from app.services.asp.antibot.detector import detect_antibot_vendor

    try:
        html = await page.content()
        title = (await page.title()).lower()
        text = ""
        try:
            text = (await page.inner_text("body"))[:4000]
        except Exception:
            pass
        if detect_antibot_vendor(html=html, url=page.url, page_text=text):
            return True
        if any(x in title for x in ("robot", "blocked", "verify", "human")):
            return True
        if "/blocked" in page.url.lower():
            return True
    except Exception:
        return True
    return False


async def _press_and_hold(page, hold_ms: int) -> bool:
    """Simulate human press-and-hold on the challenge button."""
    target = page
    for sel in _PX_IFRAME_SELECTORS:
        try:
            frame_el = page.locator(sel).first
            if await frame_el.count() > 0:
                frame = await frame_el.content_frame()
                if frame:
                    target = frame
                    break
        except Exception:
            continue

    button_selectors = (
        "button",
        "[role='button']",
        ".px-captcha-error-button",
        "#px-captcha",
        "div[tabindex='0']",
        "a",
    )
    for sel in button_selectors:
        try:
            loc = target.locator(sel)
            if await loc.count() == 0:
                continue
            box = await loc.first.bounding_box()
            if not box or box["width"] < 20 or box["height"] < 20:
                continue
            x = box["x"] + box["width"] / 2 + random.uniform(-4, 4)
            y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
            await page.mouse.move(x, y, steps=random.randint(6, 14))
            await asyncio.sleep(random.uniform(0.15, 0.45))
            await page.mouse.down()
            # Micro-jitter while holding
            elapsed = 0
            while elapsed < hold_ms:
                chunk = min(random.randint(80, 220), hold_ms - elapsed)
                await asyncio.sleep(chunk / 1000)
                elapsed += chunk
                if random.random() < 0.3:
                    await page.mouse.move(
                        x + random.uniform(-2, 2),
                        y + random.uniform(-2, 2),
                        steps=2,
                    )
            await page.mouse.up()
            await asyncio.sleep(random.uniform(0.8, 1.6))
            return True
        except Exception:
            continue
    return False


async def solve_perimeterx(page, *, max_attempts: int = 3) -> bool:
    from app.config import get_settings

    settings = get_settings()
    hold_min = settings.antibot_px_hold_ms_min
    hold_max = settings.antibot_px_hold_ms_max

    for attempt in range(max_attempts):
        hold_ms = random.randint(hold_min, hold_max)
        logger.info("PerimeterX press-and-hold attempt %d/%d hold=%dms", attempt + 1, max_attempts, hold_ms)
        pressed = await _press_and_hold(page, hold_ms)
        if not pressed:
            logger.warning("PerimeterX: could not locate press-and-hold button")
            continue
        await page.wait_for_timeout(random.randint(2000, 4500))
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        if not await _is_still_blocked(page):
            logger.info("PerimeterX challenge cleared on attempt %d", attempt + 1)
            return True
    return False
