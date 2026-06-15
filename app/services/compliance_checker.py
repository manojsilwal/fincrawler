"""Compliance detection and escalation rules."""

from __future__ import annotations

import re

BLOCK_KEYWORDS = (
    "captcha",
    "verify you are human",
    "unusual traffic",
    "access denied",
    "bot detection",
    "login required",
    "sign in to continue",
    "rate limit",
    "robot check",
    "robot or human",
    "px-captcha",
    "please enable javascript",
    "something went wrong",
    "error page | ebay",
)

GOOGLE_SHOPPING_TYPES = frozenset({"google_shopping", "google_shopping_scrape"})

INACTIVE_STATUSES = frozenset({
    "paused",
    "blocked_or_rate_limited",
    "disallowed_by_robots",
    "disallowed_by_terms",
    "error",
})


class ComplianceChecker:
    def can_use_source(self, source) -> tuple[bool, str]:
        if source.source_type in GOOGLE_SHOPPING_TYPES:
            return False, "google_shopping_direct_scrape_rejected"
        if source.status in INACTIVE_STATUSES:
            return False, f"source_status_{source.status}"
        if source.status != "active":
            return False, "source_not_active"
        return True, "ok"

    def _blob_signals(self, text: str, status_code: int | None, url: str = "") -> tuple[bool, str]:
        if status_code == 403:
            return True, "access_denied"
        if status_code == 429:
            return True, "rate_limited"
        if url and "/blocked" in url.lower():
            return True, "access_denied"

        full = text or ""
        full_len = len(full)
        head = full[:4000].lower()

        strong = (
            "robot or human",
            "verify you are human",
            "robot check",
            "px-captcha",
            "please verify you are a human",
        )
        if any(k in head for k in strong):
            return True, "captcha_detected"

        # Large retail search pages often mention "sign in" in headers — ignore on full pages
        if full_len < 15_000:
            blob = full[:12000].lower()
            for kw in BLOCK_KEYWORDS:
                if kw in blob:
                    if "captcha" in kw or "verify" in kw or "robot" in kw:
                        return True, "captcha_detected"
                    if "login" in kw or "sign in" in kw:
                        return True, "login_required"
                    if "rate limit" in kw:
                        return True, "rate_limited"
                    return True, "access_denied"
            if full_len < 2000 and any(k in blob for k in ("captcha", "security", "sign in")):
                return True, "captcha_detected"

        if full_len < 2500 and any(k in head for k in ("access denied", "something went wrong", "error page | ebay")):
            return True, "access_denied"
        return False, "ok"

    def should_escalate_after_response(
        self, response_text: str, status_code: int | None, tier_used: int, url: str = ""
    ) -> tuple[bool, str]:
        if tier_used != 1:
            return False, "ok"
        hit, reason = self._blob_signals(response_text, status_code, url)
        return hit, reason

    def should_stop_after_response(
        self, response_text: str, status_code: int | None, tier_used: int, url: str = ""
    ) -> tuple[bool, str]:
        if tier_used < 4:
            return False, "ok"
        hit, reason = self._blob_signals(response_text, status_code, url)
        return hit, reason
