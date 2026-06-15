"""ASP scrape metrics — block rates, latency, estimated cost per successful scrape."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.config import get_settings
from app.services.asp.provider_health import PROVIDER_COST_USD

logger = logging.getLogger(__name__)

_memory_totals: dict[str, dict[str, int]] = {}


def _bucket(provider: str) -> dict[str, int]:
    if provider not in _memory_totals:
        _memory_totals[provider] = {"ok": 0, "blocked": 0, "error": 0, "total_ms": 0}
    return _memory_totals[provider]


async def record_scrape(
    *,
    provider: str,
    retailer_key: str,
    status: str,
    latency_ms: float,
    block_reason: str | None = None,
) -> None:
    b = _bucket(provider)
    key = status if status in ("ok", "blocked", "error") else "error"
    b[key] = b.get(key, 0) + 1
    b["total_ms"] = b.get("total_ms", 0) + int(latency_ms)

    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pipe = client.pipeline()
        pipe.hincrby(f"fincrawler:asp:metrics:{provider}", key, 1)
        pipe.hincrbyfloat(f"fincrawler:asp:metrics:{provider}", "total_ms", latency_ms)
        if retailer_key:
            pipe.hincrby(f"fincrawler:asp:metrics:retailer:{retailer_key}", key, 1)
        pipe.expire(f"fincrawler:asp:metrics:{provider}", 604800)
        await pipe.execute()
        await client.aclose()
    except Exception:
        pass


async def get_dashboard() -> dict[str, Any]:
    """Aggregate metrics for /asp/metrics dashboard."""
    settings = get_settings()
    providers: dict[str, Any] = {}

    # Memory totals
    for name, b in _memory_totals.items():
        total = b["ok"] + b["blocked"] + b["error"]
        ok = b["ok"]
        providers[name] = {
            "attempts": total,
            "success": ok,
            "blocked": b["blocked"],
            "errors": b["error"],
            "block_rate_pct": round(100 * b["blocked"] / total, 1) if total else 0,
            "success_rate_pct": round(100 * ok / total, 1) if total else 0,
            "avg_latency_ms": round(b["total_ms"] / total) if total else 0,
            "est_cost_per_success_usd": PROVIDER_COST_USD.get(name, 0),
            "est_total_cost_usd": round(ok * PROVIDER_COST_USD.get(name, 0), 4),
        }

    budget_spent = 0.0
    try:
        import redis.asyncio as aioredis
        from datetime import datetime, timezone

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw = await client.get(f"fincrawler:asp:budget:{day}")
        if raw:
            budget_spent = float(raw)
        # Merge redis provider hashes
        for key in await client.keys("fincrawler:asp:metrics:*"):
            if ":retailer:" in key:
                continue
            name = key.split(":")[-1]
            data = await client.hgetall(key)
            if name not in providers:
                providers[name] = {}
            for field in ("ok", "blocked", "error"):
                providers[name][field] = int(data.get(field, 0))
        await client.aclose()
    except Exception:
        pass

    from app.services.asp.provider_health import health_snapshot, is_budget_exceeded
    from app.services.asp.proxy_pool import pool_status

    return {
        "generated_at": time.time(),
        "daily_budget_usd": settings.asp_daily_budget_usd,
        "daily_spent_usd": round(budget_spent, 4),
        "budget_exceeded": is_budget_exceeded(),
        "providers": providers,
        "provider_health": health_snapshot(),
        "proxy_pool": pool_status(),
    }
