# crawler.py
"""
Core crawl logic — unified through HybridRouter (compliance + tier escalation).

crawl_single  — scrape one URL via HybridRouter
crawl_parallel — fan out crawl_single with a concurrency limit
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 200_000


def _clean_text(raw: str) -> str:
    """Strip excess whitespace while preserving paragraph boundaries."""
    lines = [line.strip() for line in raw.splitlines()]
    deduped: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        deduped.append(line)
        prev_blank = is_blank
    return "\n".join(deduped)[:_MAX_TEXT_CHARS]


def _ephemeral_source(url: str, robots_policy: str = "advisory") -> SimpleNamespace:
    host = (urlparse(url).hostname or "unknown").lower().removeprefix("www.")
    return SimpleNamespace(
        id=None,
        name=f"Ephemeral ({host})",
        source_type="generic_url",
        retailer_key=f"generic:{host}",
        base_url=f"https://{host}",
        status="active",
        allowed=True,
        robots_policy=robots_policy,
        escalate_on_block=True,
        default_crawl_delay_seconds=0,
        max_requests_per_minute=60,
    )


async def _fetch_without_db(url: str, options: dict) -> dict:
    """Direct tier escalate when Postgres is unavailable (local benches)."""
    from app.services.compliance_checker import ComplianceChecker
    from app.services.crawler.compliant_fetcher import fetch_compliant
    from app.services.crawler.managed_fetcher import fetch_managed
    from app.services.crawler.js_probe import needs_js_rendering
    from app.config import get_settings

    retailer_key = str(options.get("retailer_key") or "")
    result = await fetch_compliant(url)
    text = result.get("page_text") or result.get("text") or ""
    compliance = ComplianceChecker()
    esc, esc_reason = compliance.should_escalate_after_response(
        text, result.get("http_status"), result.get("tier_used", 1), result.get("url", url)
    )
    if not esc and get_settings().enable_auto_js_probe:
        needs_js, js_reason = needs_js_rendering(result.get("html"), text)
        if needs_js:
            esc, esc_reason = True, f"js_probe:{js_reason}"
    if esc or result.get("status") != "ok":
        logger.info("DB-less escalate %s: %s", url, esc_reason or result.get("status"))
        result = await fetch_managed(url, retailer_key=retailer_key)
        result["escalated_from"] = esc_reason or "fetch_failed"
    return result


async def crawl_single(url: str, crawl_options: dict | None = None) -> dict:
    """
    Scrape a single URL via HybridRouter (Tier-1 → ASP escalation).

    ``crawl_options`` may include:
      - source_id: UUID string of an existing Source
      - retailer_key: map to a managed retailer source
      - robots_policy: override for ephemeral generic sources ("strict"|"advisory")
    """
    from app.database import SessionLocal
    from app.services.crawler.hybrid_router import hybrid_router
    from app.services.source_registry import SourceRegistry

    options = crawl_options or {}
    registry = SourceRegistry()
    db = None
    try:
        db = SessionLocal()
        source = None
        source_id = options.get("source_id")
        retailer_key = options.get("retailer_key")
        if source_id:
            import uuid

            source = registry.get(db, uuid.UUID(str(source_id)))
        elif retailer_key:
            source = registry.get_by_retailer(db, str(retailer_key))
        if source is None:
            source = registry.get_or_create_for_url(
                db,
                url,
                robots_policy=str(options.get("robots_policy") or "advisory"),
            )

        result = await hybrid_router.fetch(db, source, url)
    except Exception as db_exc:
        logger.warning(
            "crawl_single DB/HybridRouter unavailable (%s) — using direct fetch for %s",
            db_exc.__class__.__name__,
            url,
        )
        try:
            if db is not None:
                db.close()
                db = None
        except Exception:
            pass
        try:
            result = await _fetch_without_db(url, options)
        except Exception as exc:
            logger.exception("crawl_single failed for %s", url)
            return {
                "url": url,
                "status": "error",
                "error": str(exc),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
            }
    else:
        text = result.get("page_text") or result.get("text") or ""
        if text and len(text) > _MAX_TEXT_CHARS:
            text = _clean_text(text)
            result["text"] = text
            result["page_text"] = text
            result["char_count"] = len(text)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    if result.get("status") == "ok" and "crawled_at" not in result:
        result["crawled_at"] = datetime.now(timezone.utc).isoformat()
    if result.get("status") == "ok":
        result.setdefault("cache_hit", False)
        result.setdefault("title", "")
        if "text" not in result and result.get("page_text"):
            result["text"] = result["page_text"]
    return result


async def crawl_parallel(urls: list[str], max_concurrency: int = 5) -> list[dict]:
    """Crawl multiple URLs concurrently, respecting max_concurrency."""
    sem = asyncio.Semaphore(max_concurrency)

    async def _guarded(u: str) -> dict:
        async with sem:
            return await crawl_single(u)

    return await asyncio.gather(*(_guarded(u) for u in urls))


def hostname_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""
