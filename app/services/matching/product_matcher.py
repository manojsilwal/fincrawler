"""Product deduplication and matching."""

from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models import Product


class ProductMatcher:
    def find_or_create_product(self, db: Session, normalized: dict) -> dict:
        gtin = normalized.get("gtin")
        if gtin:
            existing = db.query(Product).filter(Product.gtin == str(gtin)).first()
            if existing:
                return {
                    "product_id": existing.id,
                    "match_type": "gtin_exact",
                    "confidence": 0.99,
                    "needs_review": False,
                }

        brand = (normalized.get("brand") or "").lower()
        mpn = normalized.get("mpn") or normalized.get("model_number")
        if brand and mpn:
            existing = (
                db.query(Product)
                .filter(Product.brand.ilike(brand), Product.mpn == str(mpn))
                .first()
            )
            if existing:
                return {
                    "product_id": existing.id,
                    "match_type": "mpn_brand",
                    "confidence": 0.95,
                    "needs_review": False,
                }

        nkey = normalized.get("normalized_key")
        if nkey:
            existing = db.query(Product).filter(Product.normalized_key == nkey).first()
            if existing:
                return {
                    "product_id": existing.id,
                    "match_type": "normalized_key",
                    "confidence": 0.93,
                    "needs_review": False,
                }

        title = normalized.get("canonical_title") or ""
        candidates = db.query(Product).limit(200).all()
        best = None
        best_score = 0.0
        for c in candidates:
            if brand and c.brand and brand != (c.brand or "").lower():
                continue
            score = SequenceMatcher(None, title.lower(), (c.canonical_title or "").lower()).ratio()
            if score > best_score:
                best_score = score
                best = c

        if best and best_score >= 0.92:
            return {
                "product_id": best.id,
                "match_type": "fuzzy_title",
                "confidence": best_score,
                "needs_review": False,
            }
        if best and best_score >= 0.80:
            return {
                "product_id": best.id,
                "match_type": "fuzzy_title",
                "confidence": best_score,
                "needs_review": True,
            }

        row = Product(
            canonical_title=normalized.get("canonical_title") or title,
            brand=normalized.get("brand"),
            manufacturer=normalized.get("manufacturer"),
            category=normalized.get("category"),
            gtin=str(gtin) if gtin else None,
            mpn=str(mpn) if mpn else None,
            model_number=normalized.get("model_number"),
            normalized_key=nkey,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "product_id": row.id,
            "match_type": "new_product",
            "confidence": 0.0,
            "needs_review": False,
        }
