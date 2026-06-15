"""Classify bot-wall vendor from HTML/URL markers."""

from __future__ import annotations

import re


def detect_antibot_vendor(*, html: str = "", url: str = "", page_text: str = "") -> str | None:
    blob = f"{url}\n{html}\n{page_text}".lower()

    if any(
        m in blob
        for m in (
            "px-captcha",
            "perimeterx",
            "human-challenge",
            "_pxhd",
            "_px3",
            "captcha.px-cdn.net",
            "press & hold",
            "press and hold",
        )
    ):
        return "perimeterx"

    if any(
        m in blob
        for m in (
            "datadome",
            "captcha-delivery.com",
            "geo.captcha-delivery.com",
            "dd-captcha",
            "datadome-captcha",
        )
    ):
        return "datadome"

    if any(m in blob for m in ("g-recaptcha", "recaptcha", "data-sitekey")):
        return "recaptcha"

    if any(m in blob for m in ("cf-challenge", "cloudflare", "just a moment", "turnstile")):
        return "cloudflare"

    if re.search(r'id=["\']px-captcha["\']', html, re.I):
        return "perimeterx"

    return None
