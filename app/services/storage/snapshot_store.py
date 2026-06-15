"""Persist raw HTML snapshots."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RawSnapshot


class SnapshotStore:
    def save(
        self,
        db: Session,
        source_id: uuid.UUID | None,
        url: str,
        content: str,
        content_hash: str,
        http_status: int | None,
    ) -> uuid.UUID:
        settings = get_settings()
        base = Path(settings.snapshot_dir)
        base.mkdir(parents=True, exist_ok=True)
        snap_id = uuid.uuid4()
        path = base / f"{snap_id}.html"
        path.write_text(content[:500_000], encoding="utf-8", errors="ignore")
        row = RawSnapshot(
            id=snap_id,
            source_id=source_id,
            url=url,
            content_hash=content_hash,
            storage_path=str(path),
            content_type="text/html",
            http_status=http_status,
        )
        db.add(row)
        db.commit()
        return snap_id
