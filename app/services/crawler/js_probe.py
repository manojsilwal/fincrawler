"""Detect JS-heavy / SPA shells that need browser escalation (AnyCrawl ``auto``)."""

from __future__ import annotations

import re

_SPA_ROOTS = re.compile(
    r'id=["\'](?:root|app|__next|__nuxt|main-content)["\']',
    re.I,
)
_REACT_MARKERS = re.compile(
    r"data-reactroot|ng-version=|window\.__INITIAL_STATE__|__NEXT_DATA__",
    re.I,
)


def needs_js_rendering(
    html: str | None,
    text: str | None = None,
    *,
    min_text_chars: int = 400,
) -> tuple[bool, str]:
    """Return (needs_browser, reason) after a cheap HTTP fetch."""
    html = html or ""
    text = (text or "").strip()

    if len(html) < 200:
        return True, "empty_or_tiny_html"

    if len(text) < min_text_chars:
        if _SPA_ROOTS.search(html) or _REACT_MARKERS.search(html):
            return True, "spa_shell_low_text"
        script_count = len(re.findall(r"<script", html, re.I))
        if script_count >= 8 and len(text) < min_text_chars:
            return True, "script_heavy_low_text"
        if len(text) < 80:
            return True, "near_empty_text"

    return False, ""
