"""Product and offer read APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Offer, PriceHistory, Product
from app.schemas import OfferOut, ProductOut

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), limit: int = 50):
    return db.query(Product).limit(limit).all()


@router.get("/search", response_model=list[ProductOut])
def search_products(q: str = Query(...), db: Session = Depends(get_db)):
    return db.query(Product).filter(Product.canonical_title.ilike(f"%{q}%")).limit(50).all()


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(Product, product_id)
    if not row:
        raise HTTPException(404, "product not found")
    return row


@router.get("/{product_id}/offers", response_model=list[OfferOut])
def product_offers(product_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.query(Offer).filter(Offer.product_id == product_id).all()
    return [
        OfferOut(
            id=r.id,
            product_id=r.product_id,
            merchant_name=r.merchant_name,
            title=r.title,
            price=float(r.price) if r.price is not None else None,
            currency=r.currency,
            availability=r.availability,
            url=r.url,
            shopping_score=float(r.shopping_score) if r.shopping_score else None,
        )
        for r in rows
    ]


@router.get("/{product_id}/price-history")
def product_price_history(product_id: uuid.UUID, db: Session = Depends(get_db)):
    offers = db.query(Offer).filter(Offer.product_id == product_id).all()
    offer_ids = [o.id for o in offers]
    if not offer_ids:
        return []
    rows = (
        db.query(PriceHistory)
        .filter(PriceHistory.offer_id.in_(offer_ids))
        .order_by(PriceHistory.captured_at.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "offer_id": str(r.offer_id),
            "price": float(r.price),
            "currency": r.currency,
            "availability": r.availability,
            "captured_at": r.captured_at.isoformat(),
        }
        for r in rows
    ]
