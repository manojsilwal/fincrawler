#!/usr/bin/env python3
"""
Finance crawl latency benchmark + TradeTalk LLM cascade verification.

Measures Yahoo / finance paths side-by-side and confirms FinCrawler ``llm.py``
uses the same provider fallback order as TradeTalk ``LLMClient``:
  NVIDIA → OpenRouter → GitHub Models → Gemini (GEMINI_LLM_FALLBACK)

Usage:
  cd fincrawler
  .venv/bin/python scripts/bench_finance_crawl.py
  .venv/bin/python scripts/bench_finance_crawl.py --ticker AAPL --skip-llm
  .venv/bin/python scripts/bench_finance_crawl.py --ticker MSFT --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


async def _timed(name: str, coro) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await coro
        row: dict[str, Any] = {
            "name": name,
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "latency_ms": _ms(started),
        }
        if isinstance(result, dict):
            for k in (
                "source",
                "cache_hit",
                "field_count",
                "error",
                "ticker",
                "status",
                "tier_used",
                "tier_name",
                "attempts",
                "provider",
                "model",
                "count",
                "price",
            ):
                if k in result and result[k] is not None:
                    row[k] = result[k]
            # Derive price from yahoo full payloads
            if "price" not in row and result.get("data"):
                from app.services.yahoo_finance import parse_flat_price_for_ticker

                sym = result.get("ticker") or ""
                price = parse_flat_price_for_ticker(result["data"], sym)
                if price:
                    row["price"] = price
            if "ok" in result:
                row["ok"] = bool(result["ok"])
        return row
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "ok": False,
            "latency_ms": _ms(started),
            "error": str(exc)[:300],
        }


def verify_llm_cascade() -> dict[str, Any]:
    """Assert FinCrawler cascade matches TradeTalk HTTP order."""
    from llm import (
        _FALLBACK_PROVIDER_MODEL,
        _MODEL,
        _gemini_fallback_enabled,
        _ordered_providers,
        _reset_llm_clients,
    )

    _reset_llm_clients()
    providers = _ordered_providers()
    names = [p.name for p in providers]
    models = {p.name: p.model for p in providers}

    expected_core = ["nvidia", "openrouter", "github"]
    present_core = [n for n in expected_core if n in names]
    # Relative order among configured HTTP providers must match TradeTalk
    order_ok = present_core == sorted(
        present_core, key=lambda n: expected_core.index(n)
    )
    if "gemini" in names:
        gemini_last = names[-1] == "gemini"
    else:
        gemini_last = True

    return {
        "tradetalk_order": "NVIDIA → OpenRouter → GitHub → Gemini",
        "fincrawler_providers": names,
        "models": models,
        "openrouter_model": _MODEL,
        "nvidia_model": _FALLBACK_PROVIDER_MODEL,
        "gemini_fallback_enabled": _gemini_fallback_enabled(),
        "http_order_matches_tradetalk": order_ok,
        "gemini_is_last": gemini_last,
        "ok": order_ok and gemini_last and bool(names),
    }


async def bench_llm_ping() -> dict[str, Any]:
    from llm import _MODEL, _create_chat_completion, _ordered_providers

    providers = _ordered_providers()
    started = time.perf_counter()
    resp = await _create_chat_completion(
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        model=_MODEL,
        max_tokens=25,
        temperature=0,
    )
    content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    # Best-effort: report which model string the response used if present
    used_model = getattr(resp, "model", None) or (
        providers[0].model if providers else None
    )
    return {
        "ok": bool(content) or bool(getattr(resp.choices[0].message, "reasoning", None) if resp.choices else None),
        "latency_ms": _ms(started),
        "reply": content[:80],
        "provider": "cascade",
        "model": used_model,
        "cascade": [p.name for p in providers],
    }


async def bench_finance(ticker: str, *, skip_llm: bool, skip_crawl: bool) -> dict[str, Any]:
    from app.services.yahoo_finance import (
        fetch_quote_summary_http,
        fetch_yahoo_chart_http,
        fetch_yahoo_full,
        fetch_yahoo_news,
        parse_flat_price_for_ticker,
    )

    sym = ticker.upper().strip()
    rows: list[dict[str, Any]] = []

    # 1) Fast API paths
    async def _quote_summary():
        r = await fetch_quote_summary_http(sym)
        modules = r.get("modules") or {}
        flat = {}
        if modules:
            from app.services.yahoo_finance import flatten_yahoo_modules

            flat = flatten_yahoo_modules(modules)
        price = parse_flat_price_for_ticker(flat, sym)
        return {
            "ok": bool(price),
            "source": r.get("source"),
            "price": price,
            "field_count": len(flat),
        }

    rows.append(await _timed("yahoo_quote_summary_http", _quote_summary()))

    async def _chart():
        r = await fetch_yahoo_chart_http(sym)
        flat = r.get("flat") or {}
        price = parse_flat_price_for_ticker(flat, sym) or flat.get("chart.regularMarketPrice")
        return {
            "ok": price is not None,
            "source": r.get("source"),
            "price": price,
            "field_count": len(flat),
        }

    rows.append(await _timed("yahoo_chart_http", _chart()))

    # 2) Full quote (ASP / curl / vision cascade) — cold then warm cache
    rows.append(
        await _timed(
            "yahoo_full_cold",
            fetch_yahoo_full(sym, force_refresh=True),
        )
    )
    rows.append(
        await _timed(
            "yahoo_full_warm_cache",
            fetch_yahoo_full(sym, force_refresh=False),
        )
    )

    # 3) News
    async def _news():
        articles = await fetch_yahoo_news(sym, limit=8)
        return {"ok": len(articles) > 0, "count": len(articles), "source": "yahoo_news"}

    rows.append(await _timed("yahoo_news", _news()))

    # 4) Page crawl paths (HybridRouter / compliant)
    if not skip_crawl:
        from app.services.crawler.compliant_fetcher import fetch_compliant
        from crawler import crawl_single

        url = f"https://finance.yahoo.com/quote/{sym}/"
        rows.append(await _timed("tier1_compliant_fetch", fetch_compliant(url)))
        rows.append(
            await _timed(
                "hybrid_crawl_single",
                crawl_single(url, {"robots_policy": "advisory"}),
            )
        )

        # SEC company filings landing (finance-adjacent)
        sec_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000789019&type=10-K&count=5"
        rows.append(
            await _timed(
                "sec_edgar_compliant",
                fetch_compliant(sec_url),
            )
        )

    # 5) LLM cascade ping
    llm_info = verify_llm_cascade()
    if not skip_llm:
        rows.append(await _timed("llm_cascade_ping", bench_llm_ping()))

    return {
        "ticker": sym,
        "llm_cascade": llm_info,
        "results": rows,
    }


def _print_table(payload: dict[str, Any]) -> None:
    cascade = payload.get("llm_cascade") or {}
    print("\n=== TradeTalk LLM cascade alignment ===")
    print(f"  expected: {cascade.get('tradetalk_order')}")
    print(f"  actual:   {' → '.join(cascade.get('fincrawler_providers') or [])}")
    print(f"  models:   {cascade.get('models')}")
    print(f"  match:    {cascade.get('ok')} (http_order={cascade.get('http_order_matches_tradetalk')}, gemini_last={cascade.get('gemini_is_last')})")

    print(f"\n=== Finance latency ({payload.get('ticker')}) ===")
    print(f"{'name':32} {'ok':5} {'ms':>10}  detail")
    print("-" * 90)
    for row in payload.get("results") or []:
        detail_parts = []
        for k in ("source", "price", "field_count", "count", "cache_hit", "tier_name", "provider", "model", "error"):
            if k in row:
                detail_parts.append(f"{k}={row[k]}")
        detail = "  ".join(detail_parts)
        print(
            f"{row.get('name', ''):32} {str(row.get('ok')):5} {row.get('latency_ms', 0):10.1f}  {detail[:70]}"
        )

    ok_rows = [r for r in payload.get("results") or [] if r.get("ok")]
    fail_rows = [r for r in payload.get("results") or [] if not r.get("ok")]
    if ok_rows:
        fastest = min(ok_rows, key=lambda r: r.get("latency_ms", 1e9))
        slowest = max(ok_rows, key=lambda r: r.get("latency_ms", 0))
        print(
            f"\nFastest ok: {fastest['name']} ({fastest['latency_ms']} ms) | "
            f"Slowest ok: {slowest['name']} ({slowest['latency_ms']} ms)"
        )
    if fail_rows:
        print(f"Failures ({len(fail_rows)}): " + ", ".join(r["name"] for r in fail_rows))


async def main() -> int:
    parser = argparse.ArgumentParser(description="Finance crawl latency bench")
    parser.add_argument("--ticker", default="MSFT")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-crawl", action="store_true", help="Skip page crawl / SEC")
    parser.add_argument("--json", dest="json_out", default="", help="Write full JSON report")
    args = parser.parse_args()

    payload = await bench_finance(
        args.ticker, skip_llm=args.skip_llm, skip_crawl=args.skip_crawl
    )
    _print_table(payload)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
        print(f"\nWrote {args.json_out}")

    cascade_ok = bool((payload.get("llm_cascade") or {}).get("ok"))
    # Require quote path success for exit 0
    quote_ok = any(
        r.get("name") in ("yahoo_full_cold", "yahoo_quote_summary_http", "yahoo_chart_http")
        and r.get("ok")
        for r in payload.get("results") or []
    )
    return 0 if cascade_ok and quote_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
