"""Live hybrid shop search orchestration."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.crawler.html_product_extractor import extract_product_fields
from app.services.crawler.hybrid_router import hybrid_router
from app.services.matching.product_matcher import ProductMatcher
from app.services.normalization.product_normalizer import ProductNormalizer
from app.services.ranking.product_ranker import ProductRanker
from app.services.source_registry import SourceRegistry
from llm import extract_structured
from shop_price_extract import merge_shop_extraction, prepare_llm_context, retailer_prompt_hint

logger = logging.getLogger(__name__)

DEFAULT_RETAILERS = ("amazon", "walmart", "ebay", "bestbuy", "target")

_SHOP_PROMPT = """Extract shopping/product information for "{query}" from this retail search page.
Return JSON: product_name, price (float USD), original_price, availability, seller, rating, review_count, product_url.
Ignore accessories. If not found: {{"_error": "product_not_found"}}."""

_registry = SourceRegistry()
_normalizer = ProductNormalizer()
_matcher = ProductMatcher()
_ranker = ProductRanker()


async def _extract_with_llm(crawl: dict, query: str, retailer_key: str) -> dict:
    page_text = crawl.get("page_text") or crawl.get("text") or ""
    html = crawl.get("html") or ""
    excerpt = crawl.get("price_html_excerpt") or ""
    if not html and excerpt:
        html = excerpt
    llm_context, candidates = prepare_llm_context(page_text, html, query, retailer_key)
    pre_candidates = crawl.get("price_candidates_usd") or []
    if pre_candidates:
        candidates = sorted(set(float(p) for p in pre_candidates) | set(candidates))[:20]
    hint = retailer_prompt_hint(retailer_key)
    prompt = f"{hint}\n\n{_SHOP_PROMPT.format(query=query)}" if hint else _SHOP_PROMPT.format(query=query)
    extracted = await extract_structured(
        page_text=llm_context,
        prompt=prompt,
        extra_context=f"Retailer: {retailer_key}. Query: {query}",
        task="shopping",
    )
    merged = merge_shop_extraction(extracted, candidates, query=query, retailer_key=retailer_key)
    if merged.get("price") or merged.get("product_name"):
        return merged
    fields = extract_product_fields(html, page_text)
    if fields.get("_error"):
        return merged
    return {
        "product_name": fields.get("title"),
        "price": fields.get("price"),
        "availability": fields.get("availability"),
        "rating": fields.get("rating"),
        "review_count": fields.get("review_count"),
        "product_url": crawl.get("url"),
        "price_source": "html_extract",
    }


def _persist_offer(db: Session, source, product_id, data: dict, crawl: dict):
    from app.models import Offer, PriceHistory

    title = data.get("product_name") or data.get("title") or "Unknown"
    price = data.get("price")
    now = datetime.now(timezone.utc)
    offer = (
        db.query(Offer)
        .filter(Offer.source_id == source.id, Offer.title == title)
        .first()
    )
    if not offer:
        offer = Offer(
            product_id=product_id,
            source_id=source.id,
            merchant_name=source.name,
            title=title,
            url=data.get("product_url") or crawl.get("url"),
            price=Decimal(str(price)) if price is not None else None,
            availability=data.get("availability"),
            rating=Decimal(str(data["rating"])) if data.get("rating") else None,
            review_count=data.get("review_count"),
            last_seen_at=now,
        )
        db.add(offer)
    else:
        offer.price = Decimal(str(price)) if price is not None else offer.price
        offer.last_seen_at = now
        offer.url = data.get("product_url") or offer.url
    db.commit()
    db.refresh(offer)
    if price is not None:
        db.add(
            PriceHistory(
                offer_id=offer.id,
                price=Decimal(str(price)),
                availability=data.get("availability"),
                captured_at=now,
            )
        )
        db.commit()
    return offer


async def _run_one(db: Session, retailer_key: str, query: str) -> dict:
    source = _registry.get_by_retailer(db, retailer_key)
    if not source or not source.search_url_template:
        return {
            "retailer_key": retailer_key,
            "retailer": retailer_key.title(),
            "query": query,
            "status": "error",
            "error": "source_not_configured",
            "data": None,
        }

    url = source.search_url_template.format(query=urllib.parse.quote_plus(query))
    crawl = await hybrid_router.fetch(db, source, url)
    crawl["retailer_key"] = retailer_key
    crawl["retailer"] = source.name
    crawl["query"] = query

    if crawl.get("status") != "ok":
        return {
            **{k: v for k, v in crawl.items() if k not in ("html", "page_text", "text")},
            "data": None,
        }

    data = await _extract_with_llm(crawl, query, retailer_key)
    norm = _normalizer.normalize({**data, "title": data.get("product_name")})
    match = _matcher.find_or_create_product(db, norm)
    _persist_offer(db, source, match["product_id"], data, crawl)

    result = {k: v for k, v in crawl.items() if k not in ("html", "page_text", "text")}
    result["data"] = data
    result["llm_extraction"] = "_error" not in data
    result["product_id"] = str(match["product_id"])
    return result


async def search_product(
    db: Session,
    query: str,
    retailers: list[str] | None = None,
    max_concurrency: int = 2,
) -> list[dict]:
    keys = [r for r in (retailers or list(DEFAULT_RETAILERS)) if r in DEFAULT_RETAILERS]
    sem = asyncio.Semaphore(max_concurrency)

    async def wrapped(key: str):
        async with sem:
            return await _run_one(db, key, query)

    return list(await asyncio.gather(*[wrapped(k) for k in keys]))


def search_ranked_offers(db: Session, query: str, price_max: float | None = None) -> list[dict]:
    from app.models import Offer, Product

    q = query.lower()
    offers = (
        db.query(Offer)
        .join(Product)
        .filter(Product.canonical_title.ilike(f"%{q}%"))
        .all()
    )
    rows = []
    for o in offers:
        if price_max is not None and o.price is not None and float(o.price) > price_max:
            continue
        rows.append({
            "offer_id": str(o.id),
            "merchant_name": o.merchant_name,
            "title": o.title,
            "price": float(o.price) if o.price is not None else None,
            "availability": o.availability,
            "url": o.url,
            "last_seen_at": o.last_seen_at,
            "rating": float(o.rating) if o.rating else None,
            "review_count": o.review_count,
        })
    return _ranker.rank_offers(rows)
