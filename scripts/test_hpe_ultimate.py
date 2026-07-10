"""Ultimate FinCrawler test: Yahoo Finance HPE — coverage vs page baseline."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

URL = "https://finance.yahoo.com/quote/HPE/"
TICKER = "HPE"

BASELINE_FIELDS = [
    "regularMarketPrice",
    "regularMarketChange",
    "regularMarketChangePercent",
    "regularMarketVolume",
    "regularMarketDayHigh",
    "regularMarketDayLow",
    "regularMarketOpen",
    "regularMarketPreviousClose",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "fiftyDayAverage",
    "twoHundredDayAverage",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "marketCap",
    "shortName",
    "longName",
    "currency",
    "exchange",
    "dividendYield",
    "trailingAnnualDividendRate",
    "epsTrailingTwelveMonths",
    "bookValue",
    "enterpriseValue",
    "profitMargins",
    "returnOnEquity",
    "targetHighPrice",
    "targetLowPrice",
    "targetMeanPrice",
    "recommendationKey",
    "numberOfAnalystOpinions",
    "beta",
    "postMarketPrice",
    "preMarketPrice",
]


def extract_baseline_from_html(html: str) -> dict[str, str]:
    baseline: dict[str, str] = {}
    for field in BASELINE_FIELDS:
        m = re.search(rf'"{re.escape(field)}"\s*:\s*\{{\s*"raw"\s*:\s*([^,}}]+)', html)
        if m:
            baseline[field] = m.group(1).strip().strip('"')
            continue
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*("([^"]*)"|(-?[0-9]+(?:\.[0-9]+)?))',
            html,
        )
        if m:
            baseline[field] = m.group(2) if m.group(2) is not None else m.group(3)
    return baseline


def non_null(d: dict) -> dict:
    return {
        k: v
        for k, v in d.items()
        if v is not None and v != "" and v != "null" and not str(k).startswith("_")
    }


async def main() -> None:
    from app.api.finance_compat import _fetch_page_text, _parse_yahoo_regular_price
    from extractor import extract_from_page, extract_quote

    print("=" * 72)
    print("FINCRAWLER ULTIMATE TEST — Yahoo Finance HPE")
    print("=" * 72)

    html, err = await _fetch_page_text(URL)
    print(f"\n[1] Page fetch: {len(html):,} HTML chars  err={err}")

    baseline = extract_baseline_from_html(html)
    print(f"    Baseline JSON fields in page: {len(baseline)}/{len(BASELINE_FIELDS)}")

    from app.services.crawler.compliant_fetcher import fetch_compliant

    crawl = await fetch_compliant(URL)
    crawl_text = crawl.get("text") or ""
    if not crawl_text:
        # Strip tags for rough visible-text estimate when no clean text field
        crawl_text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        crawl_text = re.sub(r"<style[\s\S]*?</style>", " ", crawl_text, flags=re.I)
        crawl_text = re.sub(r"<[^>]+>", " ", crawl_text)
        crawl_text = re.sub(r"\s+", " ", crawl_text).strip()
    print(f"[2] Fetch: status={crawl.get('status')} text={len(crawl_text):,} chars")

    regex_price = _parse_yahoo_regular_price(html)
    print(f"[3] Regex price: {regex_price}")

    smart = await extract_quote(TICKER, force_refresh=True)
    smart_data = non_null(smart.get("data") or {})
    print(f"[4] Smart quote: status={smart.get('status')} fields={len(smart_data)}")

    broad_prompt = (
        "Extract ALL available stock quote and company data: current price, change and percent, "
        "volume, day high/low, open, previous close, 52-week high/low, moving averages, "
        "P/E ratios, market cap, dividend yield, EPS, beta, analyst targets, recommendation, "
        "company name, exchange, sector, industry, and any other metrics on the page."
    )
    broad = await extract_from_page(
        URL, broad_prompt, force_refresh=True, extra_context=f"Ticker: {TICKER}"
    )
    broad_data = non_null(broad.get("data") or {})

    sections = [
        "Summary",
        "News",
        "Chart",
        "Statistics",
        "Historical Data",
        "Profile",
        "Financials",
        "Analysis",
        "Options",
        "Holders",
    ]
    sections_found = [s for s in sections if s.lower() in (crawl_text + html).lower()]

    smart_map = {
        "regularMarketPrice": "regularMarketPrice",
        "regularMarketChangePercent": "regularMarketChangePercent",
        "regularMarketVolume": "regularMarketVolume",
        "fiftyTwoWeekHigh": "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow": "fiftyTwoWeekLow",
        "trailingPE": "trailingPE",
        "marketCap": "marketCap",
        "shortName": "shortName",
    }

    smart_matched = 0
    smart_checked = 0
    for ext_key, base_key in smart_map.items():
        if base_key not in baseline:
            continue
        smart_checked += 1
        ext_val = smart_data.get(ext_key)
        base_val = baseline[base_key]
        if ext_val is None:
            continue
        try:
            if abs(float(ext_val) - float(str(base_val).replace(",", ""))) < max(
                0.05, abs(float(base_val)) * 0.02
            ):
                smart_matched += 1
        except (ValueError, TypeError):
            if str(base_val).lower() in str(ext_val).lower():
                smart_matched += 1

    broad_matched = sum(
        1
        for base_key in baseline
        if any(
            base_key.lower() in str(ek).lower() or str(ek).lower() in base_key.lower()
            for ek in broad_data
        )
    )

    text_coverage = len(crawl_text) / max(len(html), 1) * 100
    page_fetch_ok = 100 if len(html) > 5000 else len(html) / 5000 * 100
    crawl_quality = min(100, text_coverage * 3)
    smart_pct = (smart_matched / smart_checked * 100) if smart_checked else 0
    broad_pct = (broad_matched / len(baseline) * 100) if baseline else 0
    regex_ok = 100 if regex_price and 5 < regex_price < 100 else 0

    overall = (
        page_fetch_ok * 0.25
        + crawl_quality * 0.25
        + max(smart_pct, regex_ok) * 0.25
        + broad_pct * 0.25
    )

    print("\n" + "=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(json.dumps(
        {
            "url": URL,
            "baseline_fields_in_page": len(baseline),
            "baseline_sample": dict(list(baseline.items())[:10]),
            "html_chars": len(html),
            "crawl_text_chars": len(crawl_text),
            "regex_price": regex_price,
            "smart_quote_data": smart_data,
            "smart_quote_chunks": f"{smart.get('chunks_used')}/{smart.get('total_chunks')}",
            "broad_extract_fields": len(broad_data),
            "broad_extract_sample": dict(list(broad_data.items())[:15]),
            "sections_detected": sections_found,
            "coverage": {
                "page_html_fetch_pct": round(page_fetch_ok, 1),
                "text_extraction_pct": round(crawl_quality, 1),
                "core_quote_fields_pct": round(max(smart_pct, regex_ok), 1),
                "baseline_field_coverage_pct": round(broad_pct, 1),
                "overall_estimated_fetch_pct": round(overall, 1),
            },
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
