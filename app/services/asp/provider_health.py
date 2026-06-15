"""ASP provider circuit breaker, budget caps, and auto-disable on suspension."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_memory_state: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

SUSPENSION_PATTERNS = re.compile(
    r"customer_suspended|account is suspended|auth failed|billing|insufficient.?balance|quota.?exceeded",
    re.I,
)

# Retailer bot walls — provider worked; do not trip infra circuit breaker.
RETAILER_BLOCK_PATTERNS = re.compile(
    r"captcha|access_denied|no_product_list|login_required|rate_limited|"
    r"verify you are human|robot check|bot detection|blocked",
    re.I,
)

PROVIDER_COST_USD: dict[str, float] = {
    "brightdata_scraping_browser": 0.02,
    "brightdata_unlocker": 0.015,
    "external_scrapfly": 0.01,
    "proxy_http": 0.005,
    "captcha_browser": 0.003,
    "browser_grid": 0.001,
    "js_browser": 0.001,
    "http_impersonate": 0.0,
}


def _redis_key(provider: str, field: str) -> str:
    return f"fincrawler:asp:health:{provider}:{field}"


def _get_mem(provider: str) -> dict[str, Any]:
    with _lock:
        if provider not in _memory_state:
            _memory_state[provider] = {
                "failures": 0,
                "successes": 0,
                "circuit_open_until": 0.0,
                "disabled_reason": None,
            }
        return _memory_state[provider]


async def _redis_get(provider: str, field: str) -> str | None:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            return await client.get(_redis_key(provider, field))
        finally:
            await client.aclose()
    except Exception:
        return None


async def _redis_set(provider: str, field: str, value: str, ttl: int = 86400) -> None:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            await client.setex(_redis_key(provider, field), ttl, value)
        finally:
            await client.aclose()
    except Exception:
        pass


def is_circuit_open(provider: str) -> bool:
    """Return True when provider should be skipped (suspended, budget, failures)."""
    settings = get_settings()
    state = _get_mem(provider)
    if state.get("disabled_reason"):
        return True
    if time.time() < state.get("circuit_open_until", 0):
        return True
    if state.get("failures", 0) >= settings.provider_max_failures:
        return True
    return False


def get_disabled_reason(provider: str) -> str | None:
    return _get_mem(provider).get("disabled_reason")


async def record_success(provider: str, *, latency_ms: float = 0) -> None:
    state = _get_mem(provider)
    with _lock:
        state["successes"] = state.get("successes", 0) + 1
        state["failures"] = 0
        state["circuit_open_until"] = 0
    await _redis_set(provider, "successes", str(state["successes"]))
    cost = PROVIDER_COST_USD.get(provider, 0)
    if cost:
        await _increment_daily_spend(cost)


def _is_retailer_block(error: str | None) -> bool:
    if not error:
        return False
    if error.strip().lower() == "ok":
        return True
    return bool(RETAILER_BLOCK_PATTERNS.search(error))


async def record_failure(provider: str, error: str | None = None) -> None:
    # Queue backup timeouts should not disable the grid provider.
    if error and ("browser_grid_timeout" in error or "browser_grid_enqueue" in error):
        return
    if _is_retailer_block(error):
        return
    settings = get_settings()
    state = _get_mem(provider)
    with _lock:
        state["failures"] = state.get("failures", 0) + 1
        if error and SUSPENSION_PATTERNS.search(error):
            state["disabled_reason"] = "suspended_or_billing"
            state["circuit_open_until"] = time.time() + settings.provider_circuit_cooldown_seconds
            logger.warning("Provider %s auto-disabled: %s", provider, error[:120])
        elif state["failures"] >= settings.provider_max_failures:
            state["circuit_open_until"] = time.time() + settings.provider_circuit_cooldown_seconds
    await _redis_set(provider, "failures", str(state["failures"]))


async def _increment_daily_spend(amount: float) -> None:
    settings = get_settings()
    if settings.asp_daily_budget_usd <= 0:
        return
    try:
        import redis.asyncio as aioredis
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"fincrawler:asp:budget:{day}"
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            total = await client.incrbyfloat(key, amount)
            await client.expire(key, 172800)
            if total > settings.asp_daily_budget_usd:
                with _lock:
                    for p in ("brightdata_scraping_browser", "brightdata_unlocker", "external_scrapfly"):
                        _memory_state.setdefault(p, {})["disabled_reason"] = "daily_budget_exceeded"
                logger.warning("ASP daily budget exceeded ($%.2f)", total)
        finally:
            await client.aclose()
    except Exception:
        pass


def is_budget_exceeded() -> bool:
    for state in _memory_state.values():
        if state.get("disabled_reason") == "daily_budget_exceeded":
            return True
    return False


def health_snapshot() -> dict[str, Any]:
    snap = {}
    for name, state in _memory_state.items():
        snap[name] = {
            "successes": state.get("successes", 0),
            "failures": state.get("failures", 0),
            "circuit_open": is_circuit_open(name),
            "disabled_reason": state.get("disabled_reason"),
        }
    return snap


def reset_provider(provider: str) -> None:
    with _lock:
        _memory_state.pop(provider, None)
