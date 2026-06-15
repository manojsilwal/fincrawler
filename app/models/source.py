"""Source registry model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    retailer_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_url_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    robots_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    robots_policy: Mapped[str] = mapped_column(String(16), default="strict")
    escalate_on_block: Mapped[bool] = mapped_column(Boolean, default=True)
    default_crawl_delay_seconds: Mapped[int] = mapped_column(Integer, default=10)
    max_requests_per_minute: Mapped[int] = mapped_column(Integer, default=6)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
