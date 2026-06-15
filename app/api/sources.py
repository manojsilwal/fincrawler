"""Source registry endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SourceCreate, SourceOut, SourceUpdate
from app.services.source_registry import SourceRegistry

router = APIRouter(prefix="/sources", tags=["Sources"])
_registry = SourceRegistry()


@router.post("", response_model=SourceOut)
def create_source(body: SourceCreate, db: Session = Depends(get_db)):
    return _registry.create(db, body)


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    return _registry.list_all(db)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db)):
    row = _registry.get(db, source_id)
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.patch("/{source_id}", response_model=SourceOut)
def patch_source(source_id: uuid.UUID, body: SourceUpdate, db: Session = Depends(get_db)):
    row = _registry.update(db, source_id, body)
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.post("/{source_id}/review-compliance", response_model=SourceOut)
def review_compliance(source_id: uuid.UUID, db: Session = Depends(get_db)):
    row = _registry.get(db, source_id)
    if not row:
        raise HTTPException(404, "source not found")
    row.status = "active"
    row.allowed = True
    db.commit()
    db.refresh(row)
    return row


@router.post("/{source_id}/pause", response_model=SourceOut)
def pause_source(source_id: uuid.UUID, db: Session = Depends(get_db)):
    row = _registry.set_status(db, source_id, "paused")
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.post("/{source_id}/activate", response_model=SourceOut)
def activate_source(source_id: uuid.UUID, db: Session = Depends(get_db)):
    row = _registry.set_status(db, source_id, "active")
    if not row:
        raise HTTPException(404, "source not found")
    return row
