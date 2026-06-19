#!/usr/bin/env python3
"""Local pre-deploy test: Yahoo Finance HPE full data fetch."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

URL = "https://finance.yahoo.com/quote/HPE/"
TICKER = "HPE"


async def main() -> None:
    # Skip browser grid locally unless Redis worker is running.
    os.environ.setdefault("ENABLE_BROWSER_GRID", "false")
    os.environ.setdefault("ENABLE_BROWSER_TIER4", "true")

    from app.services.yahoo_finance import (
        fetch_yahoo_full,
        fetch_yahoo_quote_with_browser_session,
        flatten_yahoo_modules,
    )

    print("=" * 72)
    print("LOCAL PRE-DEPLOY TEST — Yahoo Finance HPE")
    print("=" * 72)

    from app.services.yahoo_finance import fetch_yahoo_quote_with_browser_session

    print("\n[1] Browser session + Yahoo quoteSummary API...")
    session = await fetch_yahoo_quote_with_browser_session(TICKER)
    modules = session.get("modules") or {}
    print(f"  source: {session.get('source')}")
    print(f"  meta: {json.dumps(session.get('meta'), indent=2)}")
    if modules:
        flat_preview = flatten_yahoo_modules({k: v for k, v in modules.items() if k != "symbol"})
        print(f"  flattened field count: {len(flat_preview)}")
        print(f"  price: {flat_preview.get('price.regularMarketPrice')}")

    print("\n[2] Full quote pipeline (ASP + API/vision, no news)...")
    full = await fetch_yahoo_full(TICKER, force_refresh=True)
    data = full.get("data") or {}
    price = (
        data.get("price.regularMarketPrice")
        or data.get("chart.regularMarketPrice")
        or data.get("vision.regularMarketPrice")
        or data.get("asp.regularMarketPrice")
    )

    groups: dict[str, int] = {}
    for key in data:
        prefix = key.split(".")[0]
        groups[prefix] = groups.get(prefix, 0) + 1

    report = {
        "url": URL,
        "ok": full.get("ok"),
        "source": full.get("source"),
        "field_count": full.get("field_count"),
        "modules_fetched": full.get("modules_fetched"),
        "price": price,
        "field_groups": groups,
        "asp_meta": full.get("asp_meta"),
    }
    print(json.dumps(report, indent=2, default=str))

    print("\n[3] News (separate call)...")
    from app.services.yahoo_finance import fetch_yahoo_news

    news = await fetch_yahoo_news(TICKER, limit=5, force_refresh=True)
    print(json.dumps({"ok": news.get("ok"), "count": news.get("count"), "source": news.get("source")}, indent=2))
    if news.get("articles"):
        print("\n  Sample news:")
        for article in news["articles"][:3]:
            print(f"    - {article.get('title', '')[:90]}")

    print("\n" + "=" * 72)
    if full.get("ok") and price:
        print(f"PASS — extracted {full.get('field_count')} fields, price={price}")
    elif full.get("ok"):
        print(f"PARTIAL — {full.get('field_count')} fields but no price confirmed")
    else:
        print("FAIL — no structured data extracted")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
