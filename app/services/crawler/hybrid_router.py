"""Hybrid Tier-1 → Tier-4 escalation router."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.compliance_checker import ComplianceChecker
from app.services.crawler.compliant_fetcher import fetch_compliant
from app.services.crawler.managed_fetcher import fetch_managed
from app.services.rate_limiter import RateLimiter
from app.services.robots_service import RobotsService
from app.services.source_registry import SourceRegistry
from app.services.storage.snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)

_compliance = ComplianceChecker()
_robots = RobotsService()
_rate = RateLimiter()
_registry = SourceRegistry()
_snapshots = SnapshotStore()


class HybridRouter:
    async def fetch(self, db: Session, source, url: str) -> dict:
        settings = get_settings()
        ok, reason = _compliance.can_use_source(source)
        if not ok:
            _registry.log_event(db, source.id, "source_paused", url, None, reason)
            return {"status": "rejected", "reason": reason, "url": url}

        allowed, rl_reason = await _rate.wait_or_reject(url, source)
        if not allowed:
            _registry.log_event(db, source.id, "rate_limited", url, 429, rl_reason)
            return {"status": "rejected", "reason": rl_reason, "url": url}

        use_tier4_first = (
            source.source_type == "managed_retailer_search"
            and source.robots_policy == "advisory"
        )

        if not use_tier4_first and source.robots_policy == "strict":
            can, robots_reason = await _robots.can_fetch(url, settings.crawler_user_agent)
            if not can:
                _registry.log_event(db, source.id, "robots_disallowed", url, None, robots_reason)
                if source.escalate_on_block and source.source_type == "managed_retailer_search":
                    use_tier4_first = True
                else:
                    return {"status": "rejected", "reason": robots_reason, "url": url}
        elif source.robots_policy == "advisory":
            can, robots_reason = await _robots.can_fetch(url, settings.crawler_user_agent)
            if not can:
                _registry.log_event(db, source.id, "robots_disallowed", url, None, robots_reason)
                use_tier4_first = True

        result: dict
        escalated_from = None

        if use_tier4_first:
            result = await fetch_managed(url, retailer_key=source.retailer_key or "")
            escalated_from = "advisory_or_robots"
        else:
            result = await fetch_compliant(url)
            text = result.get("page_text") or result.get("text") or ""
            esc, esc_reason = _compliance.should_escalate_after_response(
                text, result.get("http_status"), result.get("tier_used", 1), result.get("url", url)
            )
            if esc and source.escalate_on_block:
                logger.info("Escalating %s to managed fetch: %s", url, esc_reason)
                _registry.log_event(db, source.id, "captcha_detected", url, result.get("http_status"), esc_reason)
                escalated_from = esc_reason
                result = await fetch_managed(url, retailer_key=source.retailer_key or "")

        text = result.get("page_text") or result.get("text") or ""
        stop, stop_reason = _compliance.should_stop_after_response(
            text, result.get("http_status"), result.get("tier_used", 1), result.get("url", url)
        )
        if stop or result.get("status") == "error":
            reason = stop_reason if stop else result.get("error", "fetch_failed")
            event = "captcha_detected" if "captcha" in reason else "access_denied"
            _registry.log_event(db, source.id, event, url, result.get("http_status"), reason)
            result["status"] = "blocked"
            result["block_reason"] = reason
            return result

        if result.get("status") == "ok":
            content = result.get("html") or text
            h = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
            snap_id = _snapshots.save(db, source.id, result.get("url", url), content, h, result.get("http_status"))
            result["snapshot_id"] = str(snap_id)
            _registry.log_event(db, source.id, "fetch_success", url, result.get("http_status"), "ok")
            if escalated_from:
                result["escalated_from"] = escalated_from
                result["detection_hits"] = [escalated_from]

        return result


hybrid_router = HybridRouter()
