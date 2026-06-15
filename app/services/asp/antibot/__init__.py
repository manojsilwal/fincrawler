"""In-house anti-bot challenge solvers (PerimeterX, DataDome, etc.)."""

from __future__ import annotations

import logging

from app.services.asp.antibot.detector import detect_antibot_vendor
from app.services.asp.antibot.datadome import solve_datadome
from app.services.asp.antibot.perimeterx import solve_perimeterx

logger = logging.getLogger(__name__)

VENDOR_SOLVERS = {
    "perimeterx": solve_perimeterx,
    "datadome": solve_datadome,
}


async def solve_challenge(page, *, vendor: str | None = None, html: str = "", url: str = "") -> bool:
    """
    Attempt to solve a visible bot challenge on a live Playwright page.
    Returns True when the page appears unblocked after solving.
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.enable_antibot_solver:
        return False

    detected = vendor or detect_antibot_vendor(html=html, url=url)
    if not detected:
        return False

    solver = VENDOR_SOLVERS.get(detected)
    if not solver:
        logger.info("No in-house solver for vendor=%s", detected)
        return False

    logger.info("Attempting in-house antibot solve: vendor=%s url=%s", detected, (url or page.url)[:80])
    return await solver(page, max_attempts=settings.antibot_max_attempts)
