"""
Shared tiered crawl request/response envelope (see CONTRACT.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

TIER_NAMES: dict[int, str] = {
    1: "static_api",
    2: "js_rendered",
    3: "advanced_antibot",
    4: "bank_grade",
}

TIER_NAME_TO_NUM: dict[str, int] = {v: k for k, v in TIER_NAMES.items()}

DETECTION_BY_BLOCK: dict[str, list[str]] = {
    "captcha": ["captcha"],
    "cloudflare_challenge": ["captcha", "js_pow"],
    "turnstile_challenge": ["captcha", "js_pow"],
    "datadome_block": ["behavioral_ml", "browser_fingerprint"],
    "access_denied": ["ip_reputation", "automation_signals"],
    "blocked": ["behavioral_ml"],
    "login_wall": ["cookie_session"],
    "rate_limited": ["request_patterns"],
    "ip_blocked": ["ip_reputation"],
}


@dataclass
class BehaviorOptions:
    mouse: bool = True
    scroll: bool = True
    dwell_ms: int = 1200
    resource_completeness: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BehaviorOptions:
        if not data:
            return cls()
        return cls(
            mouse=bool(data.get("mouse", True)),
            scroll=bool(data.get("scroll", True)),
            dwell_ms=int(data.get("dwell_ms", 1200)),
            resource_completeness=bool(data.get("resource_completeness", True)),
        )


@dataclass
class CrawlEnvelope:
    tier: int | None = None
    tier_name: str | None = None
    max_tier: int = 4
    auto_escalate: bool = True
    session_id: str = ""
    warm_session: bool | None = None
    retailer_key: str = ""
    fingerprint_profile: str = "chrome_mac_us"
    behavior: BehaviorOptions = field(default_factory=BehaviorOptions)
    proxy: dict[str, Any] | None = None
    max_bytes: int | None = None

    @classmethod
    def from_request(cls, body: dict[str, Any] | None, profile: dict[str, Any] | None = None) -> CrawlEnvelope:
        data = body or {}
        profile = profile or {}
        tier = data.get("tier")
        tier_name = data.get("tier_name")
        if tier is None and tier_name:
            tier = TIER_NAME_TO_NUM.get(str(tier_name))
        if tier is None:
            tier = int(profile.get("default_tier", 2))
        warm = data.get("warm_session")
        if warm is None:
            warm = profile.get("warm_session", True)
        session_id = str(data.get("session_id") or "").strip()
        retailer_key = str(data.get("retailer_key") or profile.get("retailer_key") or "")
        if not session_id and retailer_key:
            session_id = f"{retailer_key}-{uuid.uuid4().hex[:12]}"
        return cls(
            tier=int(tier),
            tier_name=str(tier_name) if tier_name else TIER_NAMES.get(int(tier), "js_rendered"),
            max_tier=int(data.get("max_tier", 4)),
            auto_escalate=bool(data.get("auto_escalate", True)),
            session_id=session_id,
            warm_session=bool(warm),
            retailer_key=retailer_key,
            fingerprint_profile=str(data.get("fingerprint_profile", "chrome_mac_us")),
            behavior=BehaviorOptions.from_dict(data.get("behavior")),
            proxy=data.get("proxy") if isinstance(data.get("proxy"), dict) else None,
            max_bytes=data.get("max_bytes"),
        )

    @property
    def resolved_tier_name(self) -> str:
        if self.tier_name:
            return self.tier_name
        if self.tier is not None:
            return TIER_NAMES.get(self.tier, "js_rendered")
        return "js_rendered"


def attach_envelope(
    result: dict[str, Any],
    *,
    tier_used: int,
    session_id: str = "",
    block_reason: str | None = None,
) -> dict[str, Any]:
    """Add contract observability fields to a crawl result dict."""
    out = dict(result)
    out["tier_used"] = tier_used
    out["tier_name"] = TIER_NAMES.get(tier_used, "js_rendered")
    if session_id:
        out["session_id"] = session_id
    if block_reason:
        out["block_reason"] = block_reason
        out["detection_hits"] = DETECTION_BY_BLOCK.get(block_reason, ["behavioral_ml"])
    elif out.get("status") == "blocked":
        br = str(out.get("block_reason") or "blocked")
        out["detection_hits"] = DETECTION_BY_BLOCK.get(br, ["behavioral_ml"])
    else:
        out.setdefault("detection_hits", [])
    return out


def normalize_block_reason(raw: str | None) -> str:
    if not raw:
        return "blocked"
    mapping = {
        "captcha": "captcha_required",
        "cloudflare_challenge": "turnstile_challenge",
        "access_denied": "access_denied",
        "blocked": "access_denied",
        "login_wall": "access_denied",
    }
    return mapping.get(raw, raw)
