"""Offer scoring and ranking."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


class ProductRanker:
    def score_offer(self, offer: dict, group_prices: list[float], source_status: str = "active") -> float:
        price = offer.get("price")
        price_comp = 0.7
        if price is not None and group_prices:
            lo, hi = min(group_prices), max(group_prices)
            if hi > lo:
                price_comp = 1.0 - (float(price) - lo) / (hi - lo)
            else:
                price_comp = 1.0
        price_comp = _clamp(price_comp)

        avail_map = {"in_stock": 1.0, "preorder": 0.6, "unknown": 0.4, "out_of_stock": 0.0}
        avail = avail_map.get(str(offer.get("availability") or "unknown").lower(), 0.4)

        merchant_rel = 1.0 if source_status == "active" else 0.0
        if merchant_rel == 1.0:
            merchant_rel = 0.7

        rating = offer.get("rating")
        reviews = offer.get("review_count") or 0
        if rating is not None:
            review_quality = _clamp(float(rating) / 5.0 * min(1.0, reviews / 50.0))
        else:
            review_quality = 0.5

        last_seen = offer.get("last_seen_at")
        freshness = 0.1
        if last_seen:
            if isinstance(last_seen, datetime):
                age_h = (datetime.now(timezone.utc) - last_seen.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            else:
                age_h = 999
            if age_h <= 24:
                freshness = 1.0
            elif age_h <= 168:
                freshness = 0.7
            elif age_h <= 720:
                freshness = 0.4

        shipping = 0.5
        sp = offer.get("shipping_price")
        if sp is not None and float(sp) == 0:
            shipping = 1.0

        score = (
            0.30 * price_comp
            + 0.20 * avail
            + 0.20 * merchant_rel
            + 0.15 * review_quality
            + 0.10 * freshness
            + 0.05 * shipping
        )
        return _clamp(score)

    def rank_offers(self, offers: list[dict]) -> list[dict]:
        prices = [float(o["price"]) for o in offers if o.get("price") is not None]
        for o in offers:
            o["shopping_score"] = self.score_offer(o, prices)
        return sorted(offers, key=lambda x: x.get("shopping_score", 0), reverse=True)
