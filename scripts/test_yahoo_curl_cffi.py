#!/usr/bin/env python3
"""
Local test: Yahoo Finance via curl_cffi browser impersonation.

Usage:
  cd fincrawler
  .venv/bin/python scripts/test_yahoo_curl_cffi.py MSFT
  .venv/bin/python scripts/test_yahoo_curl_cffi.py MSFT --impersonate chrome120
  .venv/bin/python scripts/test_yahoo_curl_cffi.py MSFT --compare-httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def _test_httpx(ticker: str) -> dict:
    """Plain httpx (no curl_cffi) for comparison."""
    import httpx

    from app.services.yahoo_finance import QUOTE_MODULES, _fetch_crumb

    sym = ticker.upper().strip()
    mod_str = ",".join(QUOTE_MODULES)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"},
    ) as client:
        await client.get(f"https://finance.yahoo.com/quote/{sym}/")
        crumb = await _fetch_crumb(client)
        params: dict[str, str] = {"modules": mod_str}
        if crumb:
            params["crumb"] = crumb
        summary_ok = False
        host = None
        raw_price = None
        for h in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
            r = await client.get(f"https://{h}/v10/finance/quoteSummary/{sym}", params=params)
            if r.status_code == 200:
                rows = (r.json().get("quoteSummary") or {}).get("result") or []
                if rows:
                    summary_ok = True
                    host = h
                    pm = rows[0].get("price") or {}
                    raw = pm.get("regularMarketPrice")
                    raw_price = raw.get("raw") if isinstance(raw, dict) else raw
                    break
        chart_r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval": "1d", "range": "1d"},
        )
        chart_flat = {}
        if chart_r.status_code == 200:
            rows = (chart_r.json().get("chart") or {}).get("result") or []
            if rows:
                meta = rows[0].get("meta") or {}
                if meta.get("regularMarketPrice") is not None:
                    chart_flat["chart.regularMarketPrice"] = meta["regularMarketPrice"]
    return {
        "backend": "httpx",
        "quote_summary_ok": summary_ok,
        "host": host,
        "price": raw_price,
        "chart": chart_flat,
        "chart_status": chart_r.status_code,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test Yahoo via curl_cffi locally")
    parser.add_argument("ticker", nargs="?", default="MSFT")
    parser.add_argument(
        "--impersonate",
        default=None,
        help="curl_cffi impersonate target (default: chrome124 or YAHOO_CURL_IMPERSONATE)",
    )
    parser.add_argument(
        "--compare-httpx",
        action="store_true",
        help="Also run the existing httpx Yahoo client for comparison",
    )
    parser.add_argument(
        "--tls-check",
        action="store_true",
        help="Verify TLS fingerprint via tls.peet.ws",
    )
    args = parser.parse_args()
    sym = args.ticker.upper()

    from app.services.crawler.yahoo_curl_client import (
        fetch_yahoo_chart_curl,
        fetch_yahoo_quote_summary_curl,
        verify_tls_fingerprint,
    )

    print(f"\n=== curl_cffi Yahoo test: {sym} ===\n")

    if args.tls_check:
        print("--- TLS fingerprint (tls.peet.ws) ---")
        tls = await verify_tls_fingerprint(args.impersonate)
        print(json.dumps(tls, indent=2))
        print()

    print("--- quoteSummary (curl_cffi session) ---")
    summary = await fetch_yahoo_quote_summary_curl(sym, impersonate=args.impersonate)
    modules = summary.get("modules") or {}
    meta = summary.get("meta") or {}
    price_mod = modules.get("price") or {}
    raw_price = price_mod.get("regularMarketPrice")
    if isinstance(raw_price, dict):
        raw_price = raw_price.get("raw")
    short_name = price_mod.get("shortName")
    print(f"source:     {summary.get('source')}")
    print(f"host:       {summary.get('host')}")
    print(f"impersonate:{meta.get('impersonate') or args.impersonate or 'chrome124'}")
    print(f"warm/quote: {meta.get('warm_status')}/{meta.get('quote_status')}")
    print(f"crumb:      {meta.get('crumb_found')}")
    print(f"price:      {raw_price}")
    print(f"shortName:  {short_name}")
    if summary.get("error"):
        print(f"error:      {summary['error']}")
    print()

    print("--- chart API (curl_cffi session) ---")
    chart = await fetch_yahoo_chart_curl(sym, impersonate=args.impersonate)
    print(json.dumps(chart, indent=2, default=str))
    print()

    if args.compare_httpx:
        print("--- httpx comparison ---")
        httpx_result = await _test_httpx(sym)
        print(json.dumps(httpx_result, indent=2, default=str))
        print()

    ok = bool(modules) or bool((chart.get("flat") or {}).get("chart.regularMarketPrice"))
    print(f"RESULT: {'PASS' if ok else 'FAIL'} — {'got quote data' if ok else 'no quote data'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
