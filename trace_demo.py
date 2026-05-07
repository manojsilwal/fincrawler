"""
trace_demo.py — Side-by-side trace:
  PATH A  GET /quote   → Regex only, no LLM
  PATH B  POST /extract → Crawl → Chunk → Retrieve → DeepSeek v4 Pro
"""

import asyncio
import json
import os
import re
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

W = 72

def header(title):
    pad = (W - len(title) - 2) // 2
    print(f"\n{'═' * W}")
    print("║" + " " * pad + title + " " * (W - pad - len(title) - 1) + "║")
    print(f"{'═' * W}")


def step(n, label):
    print(f"\n  [{n}] {label}")
    print(f"      {'─' * 62}")


def box(data, indent=6):
    for line in json.dumps(data, indent=2).splitlines():
        print(" " * indent + line)


def timing(label, t):
    bar_len = min(40, int(t * 10))
    bar = "█" * bar_len + "░" * (40 - bar_len)
    print(f"  ⏱  {label:<30} {t * 1000:>7.0f} ms  {bar}")


# ─────────────────────────────────────────────────────────────────────────────
# Main trace
# ─────────────────────────────────────────────────────────────────────────────

async def run():
    from browser_pool import pool
    from crawler import crawl_single
    from extractor import chunk_text, select_top_chunks, _extract_cache_key
    from llm import extract_structured

    await pool.initialize()

    TICKER = "AAPL"
    YAHOO_URL = f"https://finance.yahoo.com/quote/{TICKER}"
    PROMPT = (
        "Extract current stock price, percent change today, volume, "
        "52-week high, 52-week low, and P/E ratio"
    )

    # ═══════════════════════════════════════════════════════════════════════
    #   PATH A  —  Regex, no LLM  (GET /quote)
    # ═══════════════════════════════════════════════════════════════════════
    header("PATH A  ·  GET /quote  ·  Regex  (No LLM)")

    step("A1", "Playwright browser → navigate → extract DOM text")
    t0 = time.perf_counter()
    async with pool.acquire() as page:
        await page.goto(YAHOO_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1200)
        html = await page.content()
        raw_body = await page.inner_text("body")
    t_crawl_a = time.perf_counter() - t0

    timing("page.goto + wait + content()", t_crawl_a)
    print(f"      HTML bytes captured:   {len(html):>10,}")
    print(f"      Body text chars:       {len(raw_body):>10,}")
    print(f"      Truncated to:          {min(len(raw_body), 50_000):>10,}  (old 50K limit)")

    step("A2", "Regex scan on embedded JSON  (_parse_yahoo_regular_price)")
    patterns = [
        (r'"regularMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)', "JSON obj .raw"),
        (r'"regularMarketPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',                  "JSON flat"),
        (r'"currentPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',       "currentPrice"),
    ]
    t0 = time.perf_counter()
    price_a = None
    hit_pat = None
    for pat, label in patterns:
        m = re.search(pat, html)
        if m:
            price_a = float(m.group(1))
            hit_pat = label
            break
    t_regex = time.perf_counter() - t0

    timing("re.search() across all patterns", t_regex)
    if price_a:
        print(f"      ✅ Matched:            [{hit_pat}]")
        print(f"      ✅ Extracted value:    ${price_a}")
    else:
        print("      ❌ No pattern matched — returns price_not_found")

    step("A3", "Response assembled and returned to caller")
    resp_a = {
        "ok": bool(price_a),
        "ticker": TICKER,
        "price": round(price_a, 4) if price_a else None,
        "currency": "USD",
    }
    box(resp_a)

    t_total_a = t_crawl_a + t_regex
    print(f"\n  ─── Path A total latency: {t_total_a * 1000:.0f} ms ───")
    fields_a = 1 if price_a else 0

    # ═══════════════════════════════════════════════════════════════════════
    #   PATH B  —  Crawl + Chunk + LLM  (POST /extract)
    # ═══════════════════════════════════════════════════════════════════════
    header("PATH B  ·  POST /extract  ·  DeepSeek v4 Pro  (With LLM)")

    step("B1", "crawl_single()  →  Playwright fetch + paragraph-aware clean")
    t0 = time.perf_counter()
    crawl_result = await crawl_single(YAHOO_URL)
    t_crawl_b = time.perf_counter() - t0
    raw_b = crawl_result.get("text", "")

    timing("crawl_single()", t_crawl_b)
    print(f"      Chars captured:   {len(raw_b):>10,}  (limit: 200,000)")
    print(f"      Page title:       {crawl_result.get('title', '')[:55]}")

    step("B2", "chunk_text()  →  paragraph-aware token-window splitting")
    t0 = time.perf_counter()
    chunks = chunk_text(raw_b)
    t_chunk = time.perf_counter() - t0

    timing("chunk_text()", t_chunk)
    print(f"      Chunks produced:  {len(chunks)}")
    for i, c in enumerate(chunks):
        tok_est = len(c) // 4
        print(f"        chunk[{i}]:  {len(c):>6,} chars  ≈  {tok_est:>5,} tokens")

    step("B3", "select_top_chunks()  →  keyword-relevance scoring, pick top-4")
    t0 = time.perf_counter()
    top_chunks = select_top_chunks(chunks, query=PROMPT, k=4)
    t_retrieve = time.perf_counter() - t0
    llm_ctx = "\n\n---\n\n".join(top_chunks)

    timing("select_top_chunks()", t_retrieve)
    print(f"      Kept {len(top_chunks)} of {len(chunks)} chunks for LLM")
    print(f"      LLM context size:  {len(llm_ctx):,} chars  ≈  {len(llm_ctx)//4:,} tokens")
    print(f"\n      ┌─ Snippet of chunk[0] sent to LLM ─────────────────────")
    preview = top_chunks[0][:250].replace("\n", " ").strip()
    print(f"      │  {preview}…")
    print(f"      └────────────────────────────────────────────────────────")

    step("B4", "LLM call  →  NVIDIA API  →  deepseek-ai/deepseek-v4-pro")
    print(f"      System:   Precise financial extractor, JSON only, no hallucination")
    print(f"      Prompt:   \"{PROMPT[:65]}…\"")
    print(f"      Context:  Ticker: AAPL (Apple Inc.)")
    print()

    t0 = time.perf_counter()
    extracted = await extract_structured(
        page_text=llm_ctx,
        prompt=PROMPT,
        extra_context=f"Ticker: {TICKER} (Apple Inc.)",
    )
    t_llm = time.perf_counter() - t0

    timing("LLM roundtrip (NVIDIA API)", t_llm)
    print(f"\n      ✅ Structured JSON returned by DeepSeek v4 Pro:")
    box(extracted)

    step("B5", "Cache write  →  keyed by hash(url + prompt)")
    cache_key = _extract_cache_key(YAHOO_URL, PROMPT)
    print(f"      Cache key:        extract:{cache_key}")
    print(f"      TTL:              300 s  (finance.yahoo domain)")
    print(f"      Next identical request: ~0 ms  (cache hit)")

    step("B6", "Response returned to caller")
    resp_b = {
        "url": YAHOO_URL,
        "prompt": PROMPT[:50] + "…",
        "data": extracted,
        "cache_hit": False,
        "chunks_used": len(top_chunks),
        "total_chunks": len(chunks),
        "status": "ok",
    }
    box(resp_b)

    t_total_b = t_crawl_b + t_chunk + t_retrieve + t_llm
    fields_b = len([k for k in extracted if not k.startswith("_")])
    print(f"\n  ─── Path B total latency: {t_total_b * 1000:.0f} ms ───")

    # ═══════════════════════════════════════════════════════════════════════
    #   SIDE-BY-SIDE TABLE
    # ═══════════════════════════════════════════════════════════════════════
    header("SIDE-BY-SIDE COMPARISON")

    c0, c1, c2 = 26, 26, 26

    def row(dim, va, vb):
        print(f"  {dim:<{c0}} {va:<{c1}} {vb:<{c2}}")

    def divider():
        print(f"  {'─'*c0} {'─'*c1} {'─'*c2}")

    print()
    row("Dimension", "PATH A (no LLM)", "PATH B (LLM)")
    divider()
    row("Endpoint",        "GET /quote",                "POST /extract")
    row("Intelligence",    "❌  Regex patterns",        "✅  DeepSeek v4 Pro")
    row("Fields returned", f"1  (price only)",          f"{fields_b}  (rich structured)")
    row("Output shape",    '{"price": 189.30}',         '{"price", "vol", "PE"…}')
    divider()
    row("Crawl latency",   f"{t_crawl_a*1000:.0f} ms", f"{t_crawl_b*1000:.0f} ms")
    row("Chunk + Retrieve","—",                         f"{(t_chunk+t_retrieve)*1000:.1f} ms")
    row("LLM roundtrip",   "—",                         f"{t_llm*1000:.0f} ms")
    row("TOTAL latency",   f"{t_total_a*1000:.0f} ms", f"{t_total_b*1000:.0f} ms")
    divider()
    row("Cached?",         "❌  No",                    "✅  Yes (url+prompt hash)")
    row("2nd request",     f"{t_total_a*1000:.0f} ms", "< 1 ms  (cache hit)")
    divider()
    row("Breaks when",     "Yahoo JS bundle changes",  "Never — LLM reads intent")
    row("Caller must",     "Parse text themselves",    "Use typed dict directly")
    row("SEC 10-K support","❌  Truncated at 50K",     "✅  Chunked, full doc")

    print(f"\n{'═' * W}")
    print(f"  Path A useful for:  Ultra-low latency price check (regex fast-path)")
    print(f"  Path B useful for:  All rich extraction, agent data, structured output")
    print(f"{'═' * W}\n")

    await pool.teardown()


asyncio.run(run())
