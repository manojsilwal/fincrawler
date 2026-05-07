"""
test_shopping.py — Live shopping crawl test.

Tests "DJI Osmo Pocket 3" across Amazon, Walmart, eBay, Best Buy, Target.
Shows per-retailer status, LLM-extracted product data, and a comparison table.
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

def section(title):
    print(f"\n  ── {title} {'─'*(W - len(title) - 6)}")

async def run():
    from browser_pool import pool
    from shop_crawler import search_product

    await pool.initialize()

    QUERY = "DJI Osmo Pocket 3"
    RETAILERS = ["amazon", "walmart", "ebay", "bestbuy", "target"]

    header(f"Shopping Crawl: '{QUERY}'")
    print(f"\n  Retailers: {', '.join(RETAILERS)}")
    print(f"  Stealth:   ✅ webdriver=undefined, chrome obj, plugins, WebGL, permissions")
    print(f"  LLM:       ✅ DeepSeek v4 Pro via NVIDIA API")
    print(f"  Strategy:  Human scroll + consent dismiss + Cloudflare wait\n")

    t_start = time.perf_counter()
    results = await search_product(query=QUERY, retailers=RETAILERS, max_concurrency=3)
    t_total = time.perf_counter() - t_start

    # ── Per-retailer detail ──────────────────────────────────────────────────
    for r in results:
        status    = r.get("status", "?")
        retailer  = r.get("retailer", r.get("retailer_key", "?"))
        http_code = r.get("http_status", "?")
        chars     = r.get("char_count", 0) or 0
        data      = r.get("data") or {}
        block     = r.get("block_reason", "")

        status_icon = {"ok": "✅", "blocked": "🚫", "error": "❌"}.get(status, "⚠️")

        section(f"{status_icon}  {retailer}  [{status.upper()}]  http={http_code}")

        if status == "ok" and data and "_error" not in data:
            print(f"     chars crawled:  {chars:,}")
            print(f"     llm_extracted:  {r.get('llm_extraction', False)}")
            print(f"\n     Extracted product data:")
            for k, v in data.items():
                if v is not None and not str(k).startswith("_"):
                    print(f"       {k:<20} {v}")
        elif status == "blocked":
            print(f"     Block reason:   {block}")
            print(f"     URL:            {r.get('url','')}")
        else:
            err = r.get("error") or (data.get("_error") if data else "unknown")
            print(f"     Error: {err}")
            if data and "_llm_raw" in data and data["_llm_raw"]:
                print(f"     LLM raw: {str(data['_llm_raw'])[:200]}")

    # ── Summary table ────────────────────────────────────────────────────────
    header("PRICE COMPARISON SUMMARY")

    c0, c1, c2, c3, c4 = 14, 8, 14, 10, 14
    def row(a, b, c, d, e):
        print(f"  {a:<{c0}} {b:<{c1}} {c:<{c2}} {d:<{c3}} {e:<{c4}}")

    row("Retailer", "Status", "Price", "Savings", "Availability")
    print(f"  {'─'*c0} {'─'*c1} {'─'*c2} {'─'*c3} {'─'*c4}")

    prices = []
    for r in results:
        status   = r.get("status", "?")
        retailer = r.get("retailer", "?")
        data     = r.get("data") or {}
        block    = r.get("block_reason", "")

        if status == "ok" and data and "_error" not in data:
            price  = data.get("price")
            orig   = data.get("original_price")
            avail  = data.get("availability", "Unknown")
            savings = data.get("savings") or (round(orig - price, 2) if orig and price and orig > price else None)

            price_str   = f"${price:.2f}"   if price   else "—"
            savings_str = f"${savings:.2f}" if savings else "—"
            if price:
                prices.append((price, retailer))
            row(retailer, "✅ ok", price_str, savings_str, str(avail)[:13])
        elif status == "blocked":
            row(retailer, f"🚫 {block[:6]}", "—", "—", "—")
        else:
            row(retailer, "❌ error", "—", "—", "—")

    print(f"  {'─'*c0} {'─'*c1} {'─'*c2} {'─'*c3} {'─'*c4}")

    if prices:
        prices.sort()
        best_price, best_retailer = prices[0]
        print(f"\n  🏆 Best price:  ${best_price:.2f} at {best_retailer}")
        if len(prices) > 1:
            spread = prices[-1][0] - prices[0][0]
            print(f"  📊 Price spread: ${spread:.2f}  ({prices[0][1]} → {prices[-1][1]})")

    ok_n      = sum(1 for r in results if r.get("status") == "ok")
    blocked_n = sum(1 for r in results if r.get("status") == "blocked")
    error_n   = sum(1 for r in results if r.get("status") == "error")

    print(f"\n  Retailers:  {ok_n} success  |  {blocked_n} blocked  |  {error_n} error")
    print(f"  Wall time:  {t_total:.1f}s  (parallel, {len(RETAILERS)} retailers)")
    print(f"\n{'═'*W}\n")

    await pool.teardown()

asyncio.run(run())
