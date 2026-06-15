"""HTML product extraction: JSON-LD → OG → meta → selectors."""

from __future__ import annotations

import json
import re
from typing import Any


def _parse_json_ld(html: str) -> dict[str, Any]:
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in ("Product", "Offer"):
                        return item
            elif isinstance(data, dict):
                if data.get("@type") == "Product" or "offers" in data:
                    return data
                graph = data.get("@graph")
                if isinstance(graph, list):
                    for item in graph:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            return item
        except json.JSONDecodeError:
            continue
    return {}


def _meta(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    return m.group(1).strip() if m else None


def _price_from_offer(offers: Any) -> float | None:
    if isinstance(offers, dict):
        p = offers.get("price") or offers.get("lowPrice")
    elif isinstance(offers, list) and offers:
        p = offers[0].get("price") if isinstance(offers[0], dict) else None
    else:
        p = None
    if p is None:
        return None
    try:
        return float(str(p).replace(",", ""))
    except ValueError:
        return None


def extract_product_fields(html: str, page_text: str = "") -> dict[str, Any]:
    ld = _parse_json_ld(html)
    title = ld.get("name") or _meta(html, "og:title") or _meta(html, "twitter:title")
    if not title and page_text:
        lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
        title = lines[0][:500] if lines else None

    brand = None
    if isinstance(ld.get("brand"), dict):
        brand = ld["brand"].get("name")
    elif isinstance(ld.get("brand"), str):
        brand = ld["brand"]

    price = _price_from_offer(ld.get("offers"))
    if price is None:
        from shop_price_extract import extract_prices_from_visible_text

        prices = extract_prices_from_visible_text(page_text or html[:50000])
        price = prices[0] if prices else None

    avail = "unknown"
    offers = ld.get("offers")
    if isinstance(offers, dict):
        av = str(offers.get("availability", "")).lower()
        if "instock" in av:
            avail = "in_stock"
        elif "outofstock" in av:
            avail = "out_of_stock"
        elif "preorder" in av:
            avail = "preorder"

    image = ld.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if not image:
        image = _meta(html, "og:image")

    sku = ld.get("sku") or ld.get("mpn")
    gtin = ld.get("gtin13") or ld.get("gtin")

    rating = None
    agg = ld.get("aggregateRating")
    if isinstance(agg, dict):
        try:
            rating = float(agg.get("ratingValue"))
        except (TypeError, ValueError):
            pass

    review_count = None
    if isinstance(agg, dict) and agg.get("reviewCount"):
        try:
            review_count = int(agg["reviewCount"])
        except (TypeError, ValueError):
            pass

    if not title:
        return {"_error": "parse_failed", "title": None}

    return {
        "title": title,
        "brand": brand,
        "price": price,
        "currency": "USD",
        "availability": avail,
        "image_url": image,
        "description": ld.get("description") or _meta(html, "og:description"),
        "sku": sku,
        "gtin": gtin,
        "mpn": ld.get("mpn"),
        "rating": rating,
        "review_count": review_count,
    }
