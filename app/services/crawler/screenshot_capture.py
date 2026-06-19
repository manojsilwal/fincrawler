"""Scroll-and-capture viewport screenshots for vision LLM extraction."""

from __future__ import annotations

import os
from typing import Any


async def capture_scrolled_screenshots(
    page,
    *,
    overlap_ratio: float | None = None,
    max_shots: int | None = None,
    settle_ms: int | None = None,
) -> list[dict[str, Any]]:
    """
    Scroll the page top-to-bottom in viewport-sized steps and capture PNGs.

    Returns list of dicts: index, scroll_y, png (bytes), bytes (size).
    Overlap between panels avoids losing content at panel boundaries.
    """
    overlap = overlap_ratio if overlap_ratio is not None else float(
        os.getenv("VISION_SCREENSHOT_OVERLAP", "0.12")
    )
    cap = max_shots if max_shots is not None else int(os.getenv("VISION_MAX_SCREENSHOTS", "15"))
    wait_ms = settle_ms if settle_ms is not None else int(os.getenv("VISION_SCROLL_SETTLE_MS", "450"))

    dims = await page.evaluate(
        """
        () => ({
            scrollHeight: Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
            ),
            viewportHeight: window.innerHeight,
        })
        """
    )
    scroll_height = int(dims["scrollHeight"])
    viewport_h = int(dims["viewportHeight"])
    if viewport_h <= 0:
        viewport_h = 720

    step = max(1, int(viewport_h * (1.0 - overlap)))
    shots: list[dict[str, Any]] = []

    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(wait_ms)

    y = 0
    while len(shots) < cap:
        png = await page.screenshot(type="png", full_page=False)
        shots.append({"index": len(shots), "scroll_y": y, "png": png, "bytes": len(png)})

        at_bottom = y + viewport_h >= scroll_height - 2
        if at_bottom or len(shots) >= cap:
            break

        y = min(y + step, max(0, scroll_height - viewport_h))
        await page.evaluate(f"window.scrollTo(0, {y})")
        await page.wait_for_timeout(wait_ms)

    # Ensure bottom panel is captured when capped before reaching end
    if shots and shots[-1]["scroll_y"] + viewport_h < scroll_height - 2 and len(shots) < cap:
        await page.evaluate(
            "window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))"
        )
        await page.wait_for_timeout(wait_ms)
        bottom_y = await page.evaluate("window.scrollY")
        if bottom_y != shots[-1]["scroll_y"]:
            png = await page.screenshot(type="png", full_page=False)
            shots.append(
                {"index": len(shots), "scroll_y": int(bottom_y), "png": png, "bytes": len(png)}
            )

    return shots
