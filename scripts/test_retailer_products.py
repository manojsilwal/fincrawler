#!/usr/bin/env python3
"""Live smoke test: fetch product listings per retailer."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal, init_db
from app.services.crawler.html_product_extractor import extract_product_fields
from app.services.crawler.hybrid_router import hybrid_router
from app.services.source_registry import SourceRegistry
from shop_price_extract import merge_shop_extraction, prepare_llm_context, retailer_prompt_hint

QUERY = os.getenv("TEST_QUERY", "dji osmo pocket 3")
RETAILERS = ("amazon", "walmart", "ebay", "bestbuy", "target")


def _heuristic_extract(crawl: dict, query: str, retailer_key: str) -> dict:
    page_text = crawl.get("page_text") or crawl.get("text") or ""
    html = crawl.get("html") or ""
    _, candidates = prepare_llm_context(page_text, html, query, retailer_key)
    merged = merge_shop_extraction({}, candidates, query=query, retailer_key=retailer_key)
    if merged.get("price") or merged.get("product_name"):
        return merged
    fields = extract_product_fields(html, page_text)
    return {
        "product_name": fields.get("title"),
        "price": fields.get("price"),
        "availability": fields.get("availability"),
        "rating": fields.get("rating"),
        "review_count": fields.get("review_count"),
        "product_url": crawl.get("url"),
        "price_source": "html_extract",
    }


async def test_retailer(db, retailer_key: str, query: str) -> dict:
    registry = SourceRegistry()
    source = registry.get_by_retailer(db, retailer_key)
    if not source:
        return {"retailer": retailer_key, "status": "error", "error": "source_not_seeded"}

    url = source.search_url_template.format(query=urllib.parse.quote_plus(query))
    crawl = await hybrid_router.fetch(db, source, url)
    row = {
        "retailer": retailer_key,
        "url": url,
        "crawl_status": crawl.get("status"),
        "http_status": crawl.get("http_status"),
        "tier_used": crawl.get("tier_used"),
        "tier_name": crawl.get("tier_name"),
        "reason": crawl.get("reason") or crawl.get("error") or crawl.get("block_reason"),
        "fetch_backend": crawl.get("fetch_backend"),
        "char_count": crawl.get("char_count"),
        "title": (crawl.get("title") or "")[:120],
    }

    if crawl.get("status") != "ok":
        return row

    data = _heuristic_extract(crawl, query, retailer_key)
    row["product_name"] = data.get("product_name")
    row["price"] = data.get("price")
    row["price_source"] = data.get("price_source")
    row["product_url"] = data.get("product_url")
    row["fetch_backend"] = crawl.get("fetch_backend")
    row["has_price"] = data.get("price") is not None
    return row


async def main():
    os.environ.setdefault("DATABASE_URL", "sqlite:///./test_retailer_fetch.db")
    init_db()
    db = SessionLocal()
    try:
        from scripts.seed_sources import main as seed

        seed()
        print(f"Query: {QUERY!r}")
        print(f"SCRAPFLY configured: {bool(os.getenv('SCRAPFLY_API_KEY', '').strip())}")
        print(f"MANAGED_PROXY configured: {bool(os.getenv('MANAGED_PROXY_URL', '').strip())}")
        print("-" * 72)
        results = []
        for key in RETAILERS:
            print(f"Fetching {key}...", flush=True)
            results.append(await test_retailer(db, key, QUERY))
        print(json.dumps(results, indent=2))
        ok = sum(1 for r in results if r.get("has_price"))
        crawled = sum(1 for r in results if r.get("crawl_status") == "ok")
        print("-" * 72)
        print(f"Crawl OK: {crawled}/{len(RETAILERS)} | Prices extracted: {ok}/{len(RETAILERS)}")
        return 0 if ok >= 1 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
