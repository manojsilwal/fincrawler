"""
Human behavior simulation for Tier 2+ browser fetches.
"""

from __future__ import annotations

import asyncio
import random

from crawl_envelope import BehaviorOptions


async def human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def human_scroll(page, behavior: BehaviorOptions) -> None:
    if not behavior.scroll:
        return
    await page.evaluate(
        """
        async () => {
            await new Promise((resolve) => {
                let pos = 0;
                const step = () => {
                    const delta = Math.floor(Math.random() * 180) + 60;
                    window.scrollBy(0, delta);
                    pos += delta;
                    if (pos < document.body.scrollHeight * 0.7) {
                        setTimeout(step, Math.floor(Math.random() * 120) + 40);
                    } else {
                        resolve();
                    }
                };
                setTimeout(step, 200);
            });
        }
        """
    )
    await human_delay(600, 1200)


async def simulate_mouse_move(page, behavior: BehaviorOptions) -> None:
    if not behavior.mouse:
        return
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        x = random.randint(80, max(100, viewport["width"] - 80))
        y = random.randint(80, max(100, viewport["height"] - 80))
        await page.mouse.move(x, y, steps=random.randint(8, 18))
    except Exception:
        pass


async def dwell(page, behavior: BehaviorOptions) -> None:
    ms = max(200, behavior.dwell_ms)
    await page.wait_for_timeout(ms + random.randint(-200, 400))


async def run_behavior(page, behavior: BehaviorOptions) -> None:
    await simulate_mouse_move(page, behavior)
    await dwell(page, behavior)
    await human_scroll(page, behavior)


async def dismiss_consent(page, selectors: list[str]) -> None:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=3_000)
                await human_delay(400, 800)
                return
        except Exception:
            continue
