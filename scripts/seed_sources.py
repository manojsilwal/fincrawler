#!/usr/bin/env python3
"""Seed managed retailer search sources."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models import Source

RETAILERS = [
    {
        "name": "Amazon Search",
        "retailer_key": "amazon",
        "search_url_template": "https://www.amazon.com/s?k={query}&ref=nb_sb_noss",
        "base_url": "https://www.amazon.com",
    },
    {
        "name": "Walmart Search",
        "retailer_key": "walmart",
        "search_url_template": "https://www.walmart.com/search?q={query}",
        "base_url": "https://www.walmart.com",
    },
    {
        "name": "eBay Search",
        "retailer_key": "ebay",
        "search_url_template": "https://www.ebay.com/sch/i.html?_nkw={query}&_sacat=0",
        "base_url": "https://www.ebay.com",
    },
    {
        "name": "Best Buy Search",
        "retailer_key": "bestbuy",
        "search_url_template": "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
        "base_url": "https://www.bestbuy.com",
    },
    {
        "name": "Target Search",
        "retailer_key": "target",
        "search_url_template": "https://www.target.com/s?searchTerm={query}",
        "base_url": "https://www.target.com",
    },
]


def main():
    init_db()
    db = SessionLocal()
    try:
        seeded = 0
        reactivated = 0
        for r in RETAILERS:
            existing = db.query(Source).filter(Source.retailer_key == r["retailer_key"]).first()
            if existing:
                # Managed retailers always escalate through the ASP engine, so a transient
                # block must never leave them permanently inactive. Self-heal on startup.
                if existing.source_type == "managed_retailer_search" and existing.status != "active":
                    existing.status = "active"
                    existing.allowed = True
                    reactivated += 1
                continue
            db.add(
                Source(
                    name=r["name"],
                    source_type="managed_retailer_search",
                    retailer_key=r["retailer_key"],
                    base_url=r["base_url"],
                    search_url_template=r["search_url_template"],
                    allowed=True,
                    status="active",
                    robots_policy="advisory",
                    escalate_on_block=True,
                    default_crawl_delay_seconds=10,
                    max_requests_per_minute=6,
                )
            )
            seeded += 1
        db.commit()
        print(f"Seeded {seeded} new retailer sources, reactivated {reactivated}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
