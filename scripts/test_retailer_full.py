#!/usr/bin/env python3
"""Full pipeline test: hybrid fetch + LLM extraction per retailer."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal, init_db
from app.services.shop_service import search_product


async def main():
    os.environ.setdefault("DATABASE_URL", "sqlite:///./test_retailer_full.db")
    init_db()
    from scripts.seed_sources import main as seed

    seed()
    db = SessionLocal()
    try:
        query = os.getenv("TEST_QUERY", "dji osmo pocket 3")
        print(f"Query: {query!r}")
        print(f"SCRAPFLY: {bool(os.getenv('SCRAPFLY_API_KEY', '').strip())}")
        print(f"LLM: {bool(os.getenv('LLM_API_KEY', '').strip() or os.getenv('OPENROUTER_API_KEY', '').strip())}")
        print("-" * 72)
        results = await search_product(db, query=query, max_concurrency=5)
        summary = []
        for r in results:
            data = r.get("data") or {}
            summary.append({
                "retailer": r.get("retailer_key"),
                "status": r.get("status"),
                "tier": r.get("tier_used"),
                "price": data.get("price"),
                "product_name": (data.get("product_name") or "")[:60],
                "error": r.get("error") or r.get("reason"),
                "llm": r.get("llm_extraction"),
            })
        print(json.dumps(summary, indent=2))
        ok = sum(1 for s in summary if s.get("price"))
        print("-" * 72)
        print(f"Prices found: {ok}/{len(summary)}")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
