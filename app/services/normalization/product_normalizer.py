"""Product normalization."""

from __future__ import annotations

import re

PROMO_WORDS = re.compile(
    r"\b(sale|deal|free shipping|limited time|clearance|new lower price)\b",
    re.I,
)


class ProductNormalizer:
    def normalize(self, raw_product: dict) -> dict:
        title = (raw_product.get("title") or raw_product.get("product_name") or "").strip()
        title = re.sub(r"\s+", " ", title)
        title = PROMO_WORDS.sub("", title).strip()
        brand = raw_product.get("brand")
        if brand:
            brand = re.sub(r"\s+", " ", str(brand).strip()).title()
        model = raw_product.get("mpn") or raw_product.get("sku") or raw_product.get("model_number")
        parts = [p.lower() for p in [brand or "", title, str(model or "")] if p]
        normalized_key = "|".join(parts)[:500] if parts else None
        return {
            **raw_product,
            "canonical_title": title,
            "brand": brand,
            "model_number": model,
            "normalized_key": normalized_key,
            "gtin": raw_product.get("gtin"),
            "mpn": raw_product.get("mpn"),
        }
