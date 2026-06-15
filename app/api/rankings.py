"""Ranking endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.shop_service import search_ranked_offers

router = APIRouter(prefix="/rankings", tags=["Rankings"])


@router.get("/search")
def rankings_search(
    q: str = Query(...),
    price_max: float | None = None,
    db: Session = Depends(get_db),
):
    rows = search_ranked_offers(db, q, price_max=price_max)
    return {"query": q, "results": rows, "total": len(rows)}


@router.post("/recompute")
def recompute_rankings(db: Session = Depends(get_db)):
    from app.models import Offer
    from app.services.ranking.product_ranker import ProductRanker
    from decimal import Decimal

    ranker = ProductRanker()
    offers = db.query(Offer).all()
    prices = [float(o.price) for o in offers if o.price is not None]
    updated = 0
    for o in offers:
        score = ranker.score_offer(
            {
                "price": float(o.price) if o.price else None,
                "availability": o.availability,
                "rating": float(o.rating) if o.rating else None,
                "review_count": o.review_count,
                "last_seen_at": o.last_seen_at,
                "shipping_price": float(o.shipping_price) if o.shipping_price else None,
            },
            prices,
        )
        o.shopping_score = Decimal(str(round(score, 4)))
        updated += 1
    db.commit()
    return {"updated": updated}
