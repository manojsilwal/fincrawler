"""Pydantic schemas for sources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str
    source_type: str
    retailer_key: Optional[str] = None
    base_url: Optional[str] = None
    api_base_url: Optional[str] = None
    search_url_template: Optional[str] = None
    robots_url: Optional[str] = None
    terms_url: Optional[str] = None
    allowed: bool = False
    status: str = "pending_review"
    robots_policy: str = "strict"
    escalate_on_block: bool = True
    default_crawl_delay_seconds: int = 10
    max_requests_per_minute: int = 6
    requires_api_key: bool = False
    notes: Optional[str] = None


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    allowed: Optional[bool] = None
    status: Optional[str] = None
    robots_policy: Optional[str] = None
    escalate_on_block: Optional[bool] = None
    default_crawl_delay_seconds: Optional[int] = None
    max_requests_per_minute: Optional[int] = None
    notes: Optional[str] = None


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    retailer_key: Optional[str] = None
    base_url: Optional[str] = None
    search_url_template: Optional[str] = None
    allowed: bool
    status: str
    robots_policy: str
    escalate_on_block: bool
    default_crawl_delay_seconds: int
    max_requests_per_minute: int
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CrawlJobUrlRequest(BaseModel):
    source_id: uuid.UUID
    url: str


class ShopSearchRequest(BaseModel):
    query: str
    retailers: Optional[list[str]] = None
    max_concurrency: int = Field(default=5, ge=1, le=5)
    per_retailer_timeout_sec: Optional[float] = Field(default=None, ge=10, le=600)


class ProductOut(BaseModel):
    id: uuid.UUID
    canonical_title: str
    brand: Optional[str] = None
    normalized_key: Optional[str] = None

    model_config = {"from_attributes": True}


class OfferOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    merchant_name: str
    title: str
    price: Optional[float] = None
    currency: str
    availability: Optional[str] = None
    url: Optional[str] = None
    shopping_score: Optional[float] = None

    model_config = {"from_attributes": True}
