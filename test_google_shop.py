"""
test_google_shop.py — Live test of the Google Shopping fallback.

Shows:
  1. Direct Google Shopping page crawl (single request → all retailers)
  2. Full shop/search with Google fallback filling in blocked retailers
"""

import asyncio
import json
import os
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

W = 72

def header(title):
    pad = (W - len(title) - 2) // 2
    print(f"\n{'═'*W}")
    print("║" + " "*pad + title + " "*(W - pad - len(title) - 1) + "║")
    print(f"{'═'*W}")

def section(label):
    print(f"\n  ── {label} {'─'*(W - len(label) - 6)}")


async def run():
    from browser_pool import pool
    from google_shop import google_shop_search

    await pool.initialize()

    QUERY = "DJI Osmo Pocket 3"

    # ═══════════════════════════════════════════════════════════════════════
    #  TEST 1: Standalone Google Shopping
    # ═══════════════════════════════════════════════════════════════════════
    header("TEST 1  ·  GET /shop/google  ·  Single Google Shopping call")

    print(f"\n  Query:    {QUERY}")
    print(f"  URL:      https://www.google.com/search?q=DJI+Osmo+Pocket+3&tbm=shop")
    print(f"  Strategy: Stealth browser → extract ALL retailer listings via LLM")

    t0 = time.perf_counter()
    result = await google_shop_search(QUERY)
    t_google = time.perf_counter() - t0

    print(f"\n  Status:          {result['status']}")
    print(f"  Chars crawled:   {result.get('char_count', 0):,}")
    print(f"  Listings found:  {result.get('total_listings', 0)}")
    print(f"  Wall time:       {t_google:.1f}s")

    listings = result.get("listings", [])

    if listings:
        print(f"\n  ─── All extracted listings (sorted by price) ────────────────")
        c0, c1, c2, c3, c4 = 16, 11, 11, 7, 10
        print(f"  {'Retailer':<{c0}} {'Price':<{c1}} {'Was':<{c1}} {'Rating':<{c2-4}} {'Avail':<{c4}}")
        print(f"  {'─'*c0} {'─'*c1} {'─'*c1} {'─'*(c2-4)} {'─'*c4}")
        for lst in listings:
            retailer  = str(lst.get("retailer",  "?"))[:c0]
            price     = f"${lst['price']:.2f}" if lst.get("price") else "—"
            orig      = f"${lst['original_price']:.2f}" if lst.get("original_price") else "—"
            rating    = f"{lst['rating']}★" if lst.get("rating") else "—"
            avail     = str(lst.get("availability", "?"))[:c4]
            print(f"  {retailer:<{c0}} {price:<{c1}} {orig:<{c1}} {rating:<{c2-4}} {avail:<{c4}}")

        # Best price
        best = min(listings, key=lambda x: x.get("price") or float("inf"))
        print(f"\n  🏆 Best price: ${best['price']:.2f} at {best.get('retailer','?')}")
        spread_min = min(l["price"] for l in listings if l.get("price"))
        spread_max = max(l["price"] for l in listings if l.get("price"))
        if spread_max > spread_min:
            print(f"  📊 Price spread: ${spread_min:.2f} – ${spread_max:.2f}  (range: ${spread_max-spread_min:.2f})")
    else:
        print("\n  ⚠️  No listings extracted. Raw LLM output check:")
        print(f"     {json.dumps(result, indent=2)[:400]}")

    # ═══════════════════════════════════════════════════════════════════════
    #  TEST 2: Full shop/search WITH Google fallback
    # ═══════════════════════════════════════════════════════════════════════
    header("TEST 2  ·  POST /shop/search  ·  Retailers + Google Fallback")

    from shop_crawler import search_product

    print(f"\n  Query:    {QUERY}")
    print(f"  Retailers: amazon, walmart, ebay, bestbuy, target")
    print(f"  Google fallback: ON  (fills blocked/errored retailers from Google data)")

    t0 = time.perf_counter()
    results = await search_product(
        query=QUERY,
        retailers=["amazon", "walmart", "ebay", "bestbuy", "target"],
        max_concurrency=3,
        google_fallback=True,
    )
    t_full = time.perf_counter() - t0

    print(f"\n  Wall time: {t_full:.1f}s  (all parallel)\n")

    status_icons = {
        "ok":             "✅ direct",
        "ok_via_google":  "🔄 google",
        "blocked":        "🚫 blocked",
        "error":          "❌ error",
    }

    for r in results:
        retailer = r.get("retailer", "?")
        rkey     = r.get("retailer_key", "?")
        status   = r.get("status", "?")
        icon     = status_icons.get(status, f"⚠️ {status}")
        data     = r.get("data") or {}

        if rkey == "google_shopping":
            g_listings = data.get("listings", [])
            section(f"🔍  Google Shopping  [{len(g_listings)} listings]")
            for gl in g_listings[:8]:
                p = f"${gl['price']:.2f}" if gl.get("price") else "—"
                print(f"       {gl.get('retailer','?'):<16} {p}  {gl.get('availability','')}")
            continue

        section(f"{icon}  {retailer}")
        if data and "_error" not in data:
            p  = data.get("price")
            op = data.get("original_price")
            s  = data.get("savings")
            print(f"     product:      {str(data.get('product_name',''))[:55]}")
            print(f"     price:        ${p:.2f}" if p else "     price:        —")
            if op:
                print(f"     was:          ${op:.2f}")
            if s:
                print(f"     savings:      ${s:.2f}")
            print(f"     availability: {data.get('availability','?')}")
            print(f"     rating:       {data.get('rating','?')} ({data.get('review_count','?')} reviews)")
            if status == "ok_via_google":
                print(f"     ↑ price sourced from Google Shopping (direct crawl was blocked)")
        elif status in ("blocked", "error"):
            print(f"     {r.get('block_reason') or r.get('error','?')}")

    ok_n = sum(1 for r in results if r.get("status") in ("ok", "ok_via_google") and r.get("retailer_key") != "google_shopping")
    bl_n = sum(1 for r in results if r.get("status") == "blocked")
    print(f"\n  Result: {ok_n} retailers with data | {bl_n} still blocked | {t_full:.1f}s total")
    print(f"\n{'═'*W}\n")

    await pool.teardown()


asyncio.run(run())
