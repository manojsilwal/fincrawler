"""Source registry CRUD helpers."""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Source
from app.schemas import SourceCreate, SourceUpdate


class SourceRegistry:
    def create(self, db: Session, data: SourceCreate) -> Source:
        row = Source(**data.model_dump())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def get(self, db: Session, source_id: uuid.UUID) -> Source | None:
        return db.get(Source, source_id)

    def get_or_create_for_url(
        self,
        db: Session,
        url: str,
        *,
        robots_policy: str = "advisory",
    ) -> Source:
        """Resolve a Source for arbitrary URL fetches (Firecrawl / legacy crawl_single).

        Prefer an active managed retailer whose base_url host matches; otherwise
        ensure a per-host generic source so HybridRouter can apply compliance.
        """
        host = (urlparse(url).hostname or "unknown").lower()
        if host.startswith("www."):
            host = host[4:]

        # Match known retailers by base_url host
        for src in (
            db.query(Source)
            .filter(Source.status == "active", Source.base_url.isnot(None))
            .all()
        ):
            try:
                src_host = (urlparse(src.base_url or "").hostname or "").lower()
                if src_host.startswith("www."):
                    src_host = src_host[4:]
                if src_host and (host == src_host or host.endswith("." + src_host)):
                    return src
            except Exception:
                continue

        key = f"generic:{host}"
        existing = (
            db.query(Source)
            .filter(Source.retailer_key == key)
            .first()
        )
        if existing:
            if existing.status != "active":
                existing.status = "active"
                existing.allowed = True
                db.commit()
                db.refresh(existing)
            return existing

        row = Source(
            name=f"Generic ({host})",
            source_type="generic_url",
            retailer_key=key,
            base_url=f"https://{host}",
            robots_url=f"https://{host}/robots.txt",
            allowed=True,
            status="active",
            robots_policy=robots_policy,
            escalate_on_block=True,
            default_crawl_delay_seconds=5,
            max_requests_per_minute=12,
            notes="Auto-created for HybridRouter URL fetches",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def get_by_retailer(self, db: Session, retailer_key: str) -> Source | None:
        active = (
            db.query(Source)
            .filter(Source.retailer_key == retailer_key, Source.status == "active")
            .first()
        )
        if active:
            return active
        # Managed retailers always escalate through the ASP engine; a transient block
        # should never make them disappear. Auto-heal an inactive managed source.
        managed = (
            db.query(Source)
            .filter(
                Source.retailer_key == retailer_key,
                Source.source_type == "managed_retailer_search",
            )
            .first()
        )
        if managed and managed.status != "active":
            managed.status = "active"
            managed.allowed = True
            db.commit()
            db.refresh(managed)
        return managed

    def list_all(self, db: Session) -> list[Source]:
        return db.query(Source).order_by(Source.name).all()

    def update(self, db: Session, source_id: uuid.UUID, data: SourceUpdate) -> Source | None:
        row = db.get(Source, source_id)
        if not row:
            return None
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        db.commit()
        db.refresh(row)
        return row

    def set_status(self, db: Session, source_id: uuid.UUID, status: str) -> Source | None:
        row = db.get(Source, source_id)
        if not row:
            return None
        row.status = status
        if status == "active":
            row.allowed = True
        db.commit()
        db.refresh(row)
        return row

    def log_event(self, db: Session, source_id, event_type: str, url: str | None, http_status, message: str):
        from app.models import CrawlEvent

        ev = CrawlEvent(
            source_id=source_id,
            url=url,
            event_type=event_type,
            http_status=http_status,
            message=message,
        )
        db.add(ev)
        if event_type in ("captcha_detected", "access_denied", "rate_limited") and source_id:
            src = db.get(Source, source_id)
            if src and src.source_type not in ("managed_retailer_search",):
                src.status = "blocked_or_rate_limited"
        db.commit()
        return ev
