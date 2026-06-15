"""DataDome slider/puzzle challenge solver."""

from __future__ import annotations

import asyncio
import io
import logging
import random

logger = logging.getLogger(__name__)

_DD_IFRAME_SELECTORS = (
    "iframe[src*='captcha-delivery.com']",
    "iframe[src*='datadome']",
    "iframe[title*='DataDome']",
    "#ddChallengeContainer iframe",
)


async def _find_challenge_frame(page):
    for sel in _DD_IFRAME_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                frame = await loc.content_frame()
                if frame:
                    return frame
        except Exception:
            continue
    return page


def _detect_gap_offset(image_bytes: bytes) -> int | None:
    """Estimate horizontal slider offset via edge-density scan."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        logger.warning("Pillow/numpy not installed — DataDome gap detection unavailable")
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        arr = np.array(img, dtype=np.float32)
        h, w = arr.shape
        if w < 80 or h < 40:
            return None
        # Focus on puzzle strip (middle band)
        band = arr[int(h * 0.25) : int(h * 0.85), :]
        grad = np.abs(np.diff(band, axis=1))
        col_score = grad.mean(axis=0)
        # Ignore edges of image
        margin = int(w * 0.08)
        search = col_score[margin : w - margin]
        if len(search) < 10:
            return None
        peak = int(search.argmax()) + margin
        return peak
    except Exception:
        logger.debug("DataDome gap detection failed", exc_info=True)
        return None


async def _drag_slider(page, target_frame, offset_px: int) -> bool:
    slider_selectors = (
        ".slider",
        ".dd-slider",
        "[class*='slider']",
        "[role='slider']",
        "div.captcha__slider",
    )
    for sel in slider_selectors:
        try:
            loc = target_frame.locator(sel).first
            if await loc.count() == 0:
                continue
            box = await loc.bounding_box()
            if not box:
                continue
            start_x = box["x"] + box["width"] * 0.15
            start_y = box["y"] + box["height"] / 2
            end_x = start_x + offset_px
            await page.mouse.move(start_x, start_y, steps=random.randint(5, 12))
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await page.mouse.down()
            steps = random.randint(18, 32)
            for i in range(1, steps + 1):
                t = i / steps
                eased = t * t * (3 - 2 * t)  # smoothstep
                x = start_x + (end_x - start_x) * eased + random.uniform(-1, 1)
                y = start_y + random.uniform(-1, 1)
                await page.mouse.move(x, y, steps=1)
                await asyncio.sleep(random.uniform(0.01, 0.04))
            await page.mouse.up()
            await asyncio.sleep(random.uniform(1.0, 2.0))
            return True
        except Exception:
            continue
    return False


async def solve_datadome(page, *, max_attempts: int = 3) -> bool:
    from app.services.asp.antibot.detector import detect_antibot_vendor

    for attempt in range(max_attempts):
        frame = await _find_challenge_frame(page)
        offset = None
        try:
            png = await frame.locator("canvas, img").first.screenshot(timeout=5000)
            offset = _detect_gap_offset(png)
        except Exception:
            logger.debug("DataDome screenshot failed", exc_info=True)

        if offset is None:
            # Fallback: drag ~60% of typical puzzle width
            offset = random.randint(120, 220)
        else:
            offset = int(offset * 0.85) + random.randint(-6, 6)

        logger.info("DataDome slider attempt %d/%d offset=%dpx", attempt + 1, max_attempts, offset)
        dragged = await _drag_slider(page, frame, offset)
        if not dragged:
            continue

        await page.wait_for_timeout(random.randint(2000, 4000))
        try:
            html = await page.content()
            if not detect_antibot_vendor(html=html, url=page.url):
                logger.info("DataDome challenge cleared on attempt %d", attempt + 1)
                return True
        except Exception:
            pass

    return False
