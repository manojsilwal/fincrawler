"""Source registry CRUD helpers."""

from __future__ import annotations

import uuid

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

    def get_by_retailer(self, db: Session, retailer_key: str) -> Source | None:
        return (
            db.query(Source)
            .filter(Source.retailer_key == retailer_key, Source.status == "active")
            .first()
        )

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
