"""Browser fingerprint profiles with per-retailer rotation."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserFingerprint:
    user_agent: str
    viewport: dict
    locale: str
    timezone_id: str
    platform: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    device_memory: int
    hardware_concurrency: int


_PROFILES: list[BrowserFingerprint] = [
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        platform="Win32",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        device_memory=8,
        hardware_concurrency=8,
    ),
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/Los_Angeles",
        platform="MacIntel",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"macOS"',
        device_memory=8,
        hardware_concurrency=10,
    ),
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        viewport={"width": 1536, "height": 864},
        locale="en-US",
        timezone_id="America/Chicago",
        platform="Win32",
        sec_ch_ua='"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        device_memory=16,
        hardware_concurrency=12,
    ),
    BrowserFingerprint(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/Denver",
        platform="Linux x86_64",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Linux"',
        device_memory=8,
        hardware_concurrency=8,
    ),
]

_rotation_index: dict[str, int] = {}


def pick_fingerprint(retailer_key: str = "", seed: str | None = None) -> BrowserFingerprint:
    """Pick a fingerprint — sticky per retailer when key provided."""
    if retailer_key:
        idx = _rotation_index.get(retailer_key, 0) % len(_PROFILES)
        _rotation_index[retailer_key] = idx + 1
        return _PROFILES[idx]
    if seed:
        h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
        return _PROFILES[h % len(_PROFILES)]
    return random.choice(_PROFILES)


def fingerprint_to_context_kwargs(fp: BrowserFingerprint) -> dict:
    return {
        "user_agent": fp.user_agent,
        "viewport": fp.viewport,
        "locale": fp.locale,
        "timezone_id": fp.timezone_id,
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": fp.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": fp.sec_ch_ua_platform,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        },
    }


def fingerprint_stealth_overrides_js(fp: BrowserFingerprint) -> str:
    return f"""
    try {{ Object.defineProperty(navigator, 'platform', {{ get: () => '{fp.platform}', configurable: true }}); }} catch(_) {{}}
    try {{ Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {fp.device_memory} }}); }} catch(_) {{}}
    try {{ Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {fp.hardware_concurrency} }}); }} catch(_) {{}}
    """
