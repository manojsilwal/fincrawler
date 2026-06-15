"""
Tier router: selects fetcher by tier, escalates on block signals, attaches observability envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from crawl_envelope import CrawlEnvelope, attach_envelope, normalize_block_reason
from fetchers.tier1_curl_cffi import fetch_tier1
from fetchers.tier2_scrapling import fetch_tier2
from fetchers.tier3_stealth_browser import fetch_retailer_search, fetch_tier3
from fetchers.tier4_managed import fetch_tier4
from profiles import get_profile

logger = logging.getLogger(__name__)

_FETCHERS = {
    1: fetch_tier1,
    2: fetch_tier2,
    3: fetch_tier3,
    4: fetch_tier4,
}


async def _run_tier(tier: int, url: str, envelope: CrawlEnvelope, retailer_config: dict | None) -> dict:
    fetcher = _FETCHERS.get(tier, fetch_tier3)
    if tier in (2, 3):
        return await fetcher(url, envelope, retailer_config=retailer_config)
    return await fetcher(url, envelope)


def _should_escalate(result: dict[str, Any], tier: int, max_tier: int, auto_escalate: bool) -> bool:
    if not auto_escalate:
        return False
    if tier >= max_tier:
        return False
    return result.get("status") == "blocked"


async def fetch_with_escalation(
    url: str,
    envelope: CrawlEnvelope,
    retailer_config: dict | None = None,
) -> dict:
    tier = int(envelope.tier or 2)
    max_tier = int(envelope.max_tier or 4)
    last: dict[str, Any] = {"url": url, "status": "error", "error": "not_attempted"}

    while tier <= max_tier:
        logger.info(
            "fetch tier=%s url=%s retailer=%s session=%s",
            tier,
            url,
            envelope.retailer_key,
            envelope.session_id,
        )
        result = await _run_tier(tier, url, envelope, retailer_config)
        last = result
        if not _should_escalate(result, tier, max_tier, envelope.auto_escalate):
            break
        tier += 1

    tier_used = tier if last.get("status") != "blocked" else min(tier, max_tier)
    if last.get("status") == "blocked" and envelope.auto_escalate:
        tier_used = min(tier, max_tier)

    block_reason = None
    if last.get("status") == "blocked":
        block_reason = normalize_block_reason(str(last.get("block_reason") or "blocked"))

    return attach_envelope(
        last,
        tier_used=tier_used,
        session_id=envelope.session_id,
        block_reason=block_reason,
    )


async def fetch_retailer(
    retailer_key: str,
    query: str,
    request_body: dict[str, Any] | None = None,
) -> dict:
    profile = get_profile(retailer_key)
    if not profile:
        return {
            "retailer_key": retailer_key,
            "status": "error",
            "error": "unknown_retailer",
            "tier_used": 0,
            "tier_name": "unknown",
            "detection_hits": [],
        }
    envelope = CrawlEnvelope.from_request(request_body, profile)
    envelope.retailer_key = retailer_key

    tier = int(envelope.tier or profile.get("default_tier", 2))
    max_tier = int(envelope.max_tier or 4)
    if retailer_key == "google_shopping":
        # Tier 4 httpx strips merchant/price structure; keep Google on Playwright.
        max_tier = min(max_tier, 3)
    retailer_config = {**profile, "retailer_key": retailer_key}
    last: dict[str, Any] = {"status": "error", "error": "not_attempted"}

    while tier <= max_tier:
        envelope.tier = tier
        if tier == 3 or (tier == 2 and retailer_key in ("amazon", "walmart", "target", "bestbuy", "ebay")):
            result = await fetch_retailer_search(retailer_key, query, envelope, retailer_config)
        else:
            import urllib.parse

            encoded = urllib.parse.quote_plus(query)
            url = profile["search_url"].format(query=encoded)
            result = await fetch_with_escalation(url, envelope, retailer_config)
            result["retailer"] = profile.get("name", retailer_key)
            result["retailer_key"] = retailer_key
            result["query"] = query
        last = result
        if not _should_escalate(result, tier, max_tier, envelope.auto_escalate):
            break
        tier += 1

    tier_used = tier
    if last.get("status") == "ok":
        tier_used = int(last.get("tier_used") or tier)
    block_reason = None
    if last.get("status") == "blocked":
        block_reason = normalize_block_reason(str(last.get("block_reason") or "blocked"))
    return attach_envelope(
        last,
        tier_used=tier_used,
        session_id=envelope.session_id,
        block_reason=block_reason,
    )


async def fetch_url(url: str, request_body: dict[str, Any] | None = None) -> dict:
    profile = None
    retailer_key = (request_body or {}).get("retailer_key")
    if retailer_key:
        profile = get_profile(str(retailer_key))
    envelope = CrawlEnvelope.from_request(request_body, profile)
    return await fetch_with_escalation(url, envelope, profile)
