"""Yahoo Finance quote extraction — API + browser + HTML fallbacks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

_YAHOO_QUOTE_URL_RE = re.compile(
    r"finance\.yahoo\.com/quote/(?P<ticker>[A-Za-z0-9.\-^=]+)",
    re.I,
)

# Modules available on Yahoo quoteSummary (v10).
QUOTE_MODULES = (
    "price",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "recommendationTrend",
    "earningsTrend",
    "calendarEvents",
    "assetProfile",
    "earningsHistory",
    "upgradeDowngradeHistory",
    "majorHoldersBreakdown",
    "institutionOwnership",
    "fundOwnership",
    "secFilings",
)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

YAHOO_RETAILER_KEY = "yahoo_finance"

_PRICE_FIELD_KEYS = (
    "price.regularMarketPrice",
    "dom.regularMarketPrice",
    "asp.regularMarketPrice",
    "html.regularMarketPrice",
    "chart.regularMarketPrice",
    "vision.regularMarketPrice",
)


def ticker_from_yahoo_url(url: str) -> str | None:
    m = _YAHOO_QUOTE_URL_RE.search(url or "")
    if not m:
        return None
    sym = unquote(m.group("ticker")).upper().strip().rstrip("/")
    return sym.split("/")[0] or None


def is_yahoo_quote_url(url: str) -> bool:
    return ticker_from_yahoo_url(url) is not None


def _unwrap_yahoo_value(obj: Any) -> Any:
    if isinstance(obj, dict):
        if "raw" in obj:
            return obj["raw"]
        if "fmt" in obj and len(obj) <= 3:
            return obj["fmt"]
    return obj


def flatten_yahoo_modules(modules: dict[str, Any]) -> dict[str, Any]:
    """Flatten quoteSummary module tree to dot-notation keys with raw values."""
    flat: dict[str, Any] = {}

    def walk(obj: Any, prefix: str) -> None:
        if obj is None:
            return
        if isinstance(obj, dict):
            if set(obj.keys()) <= {"raw", "fmt", "longFmt"} and ("raw" in obj or "fmt" in obj):
                flat[prefix.rstrip(".")] = _unwrap_yahoo_value(obj)
                return
            for key, val in obj.items():
                walk(val, f"{prefix}{key}.")
        elif isinstance(obj, list):
            if not obj:
                flat[prefix.rstrip(".")] = []
                return
            if all(isinstance(x, dict) for x in obj):
                flat[prefix.rstrip(".")] = [
                    flatten_yahoo_modules(x) if any(isinstance(v, dict) for v in x.values()) else x
                    for x in obj
                ]
            else:
                flat[prefix.rstrip(".")] = obj
        else:
            flat[prefix.rstrip(".")] = obj

    for mod_name, mod_body in modules.items():
        if isinstance(mod_body, dict):
            walk(mod_body, f"{mod_name}.")
    return flat


def parse_price_for_symbol(html: str, symbol: str) -> dict[str, Any]:
    """
    Extract quote fields scoped to *symbol* from embedded Yahoo JSON blobs.
    Avoids picking unrelated tickers (e.g. Bitcoin widgets on the same page).
    """
    if not html or not symbol:
        return {}

    sym = symbol.upper()
    out: dict[str, Any] = {}

    # Block anchored on "symbol":"HPE" … regularMarketPrice …
    block_pat = rf'"symbol"\s*:\s*"{re.escape(sym)}"[\s\S]{{0,4000}}'
    for block_m in re.finditer(block_pat, html, re.I):
        block = block_m.group(0)
        for field in (
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
            "trailingPE",
            "forwardPE",
            "marketCap",
            "shortName",
            "longName",
        ):
            m = re.search(
                rf'"{field}"\s*:\s*(?:\{{\s*"raw"\s*:\s*([^,}}]+)|"([^"]+)"|(-?[0-9]+(?:\.[0-9]+)?))',
                block,
            )
            if m:
                raw = next(g for g in m.groups() if g is not None)
                raw = raw.strip().strip('"')
                try:
                    out[field] = float(raw) if re.match(r"^-?[0-9]+(\.[0-9]+)?$", raw) else raw
                except ValueError:
                    out[field] = raw

    # fin-streamer elements: <fin-streamer data-symbol="HPE" data-field="regularMarketPrice" value="47.41">
    for m in re.finditer(
        rf'<fin-streamer[^>]+data-symbol="{re.escape(sym)}"[^>]+data-field="([^"]+)"[^>]+value="([^"]+)"',
        html,
        re.I,
    ):
        field, val = m.group(1), m.group(2)
        try:
            out[field] = float(val.replace(",", ""))
        except ValueError:
            out[field] = val

    for m in re.finditer(
        rf'data-field="([^"]+)"[^>]+data-symbol="{re.escape(sym)}"[^>]+value="([^"]+)"',
        html,
        re.I,
    ):
        field, val = m.group(1), m.group(2)
        if field not in out:
            try:
                out[field] = float(val.replace(",", ""))
            except ValueError:
                out[field] = val

    return out


def parse_yahoo_regular_price(page_text: str, symbol: str | None = None) -> float | None:
    if not page_text:
        return None
    if symbol:
        scoped = parse_price_for_symbol(page_text, symbol)
        price = scoped.get("regularMarketPrice")
        if isinstance(price, (int, float)) and price > 0:
            return float(price)

    patterns = (
        r'"regularMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"regularMarketPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"currentPrice"\s*:\s*\{\s*"raw"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    )
    for pat in patterns:
        m = re.search(pat, page_text)
        if m:
            try:
                v = float(m.group(1))
                return v if v > 0 else None
            except ValueError:
                continue
    return None


async def _yahoo_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0),
        headers={"User-Agent": _BROWSER_UA, "Accept": "application/json,*/*"},
    )


async def _fetch_crumb(client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        if r.status_code == 200 and r.text and "Too Many" not in r.text:
            return r.text.strip()
    except Exception:
        logger.debug("getcrumb failed", exc_info=True)
    return None


async def fetch_quote_summary_http(
    ticker: str,
    modules: tuple[str, ...] = QUOTE_MODULES,
) -> dict[str, Any]:
    """Fetch quoteSummary via Yahoo JSON API (requires session cookie + crumb)."""
    sym = ticker.upper().strip()
    mod_str = ",".join(modules)
    async with await _yahoo_client() as client:
        await client.get(f"https://finance.yahoo.com/quote/{sym}/")
        crumb = await _fetch_crumb(client)
        params: dict[str, str] = {"modules": mod_str}
        if crumb:
            params["crumb"] = crumb
        for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
            url = f"https://{host}/v10/finance/quoteSummary/{sym}"
            try:
                r = await client.get(url, params=params)
            except httpx.RequestError as exc:
                logger.warning("quoteSummary request failed (%s): %s", host, exc)
                continue
            if r.status_code == 200:
                payload = r.json()
                result = (payload.get("quoteSummary") or {}).get("result") or []
                if result:
                    return {"modules": result[0], "source": "yahoo_api", "host": host}
            logger.info("quoteSummary %s returned %s for %s", host, r.status_code, sym)
    return {}


def parse_fin_streamers_from_html(html: str, symbol: str) -> dict[str, Any]:
    """Parse hydrated fin-streamer widgets from rendered HTML."""
    if not html or not symbol:
        return {}
    sym = symbol.upper()
    out: dict[str, Any] = {}
    patterns = (
        rf'<fin-streamer[^>]+data-symbol="{re.escape(sym)}"[^>]+data-field="([^"]+)"[^>]+value="([^"]+)"',
        rf'data-field="([^"]+)"[^>]+data-symbol="{re.escape(sym)}"[^>]+value="([^"]+)"',
        rf'<fin-streamer[^>]+data-field="([^"]+)"[^>]+value="([^"]+)"[^>]+data-symbol="{re.escape(sym)}"',
    )
    for pat in patterns:
        for field, val in re.findall(pat, html, re.I):
            if field in out:
                continue
            cleaned = str(val).strip().replace(",", "").replace("$", "")
            try:
                out[field] = float(cleaned)
            except ValueError:
                out[field] = val.strip()
    return out


def _coerce_price_value(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _has_quote_price(flat: dict[str, Any]) -> bool:
    for key in _PRICE_FIELD_KEYS:
        if _coerce_price_value(flat.get(key)):
            return True
    return any(
        k.endswith("regularMarketPrice") and _coerce_price_value(v)
        for k, v in flat.items()
    )


def _strip_news_from_flat(flat: dict[str, Any]) -> dict[str, Any]:
    """Quote/API fallback paths never return news — use fetch_yahoo_news() instead."""
    return {
        k: v
        for k, v in flat.items()
        if k != "news"
        and not str(k).startswith("vision.news")
        and ".news." not in str(k)
        and not str(k).endswith(".news")
    }


def _yahoo_api_succeeded(modules: dict[str, Any]) -> bool:
    """True when quoteSummary returned module payload."""
    return bool(modules)


def _vision_fallback_enabled() -> bool:
    return os.getenv("VISION_FALLBACK_ENABLED", "true").lower() not in ("0", "false", "no")


def _merge_flat_with_vision(flat: dict[str, Any], vision_flat: dict[str, Any]) -> dict[str, Any]:
    """Merge vision fields after API failure; vision quote fields take precedence."""
    merged = dict(flat)
    merged.update(vision_flat)
    vprice = _coerce_price_value(vision_flat.get("vision.regularMarketPrice"))
    if vprice:
        merged["vision.regularMarketPrice"] = vprice
        for stale in ("asp.regularMarketPrice", "html.regularMarketPrice", "dom.regularMarketPrice"):
            if stale in merged and _coerce_price_value(merged.get(stale)) != vprice:
                merged.pop(stale, None)
    return _strip_news_from_flat(merged)


async def _run_vision_fallback(
    ticker: str,
    flat: dict[str, Any],
    meta: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """
    Permanent fallback: scroll + multi-screenshot vision when Yahoo API paths fail.
    """
    if not _vision_fallback_enabled():
        meta["vision_fallback_skipped"] = True
        return flat, None

    meta["vision_fallback"] = True
    meta["api_failed"] = True
    logger.info("Yahoo API failed for %s — running scroll+screenshot vision fallback", ticker)

    vision = await fetch_yahoo_via_screenshot(ticker)
    meta.update({f"vision_{k}": v for k, v in (vision.get("meta") or {}).items()})

    vision_flat = vision.get("flat") or {}
    if not vision_flat or vision.get("source") == "yahoo_screenshot_failed":
        meta["vision_fallback_ok"] = False
        return flat, None

    meta["vision_fallback_ok"] = True
    return _merge_flat_with_vision(flat, vision_flat), vision.get("source", "yahoo_screenshot_vision")


def extract_quote_from_fetch_result(result: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Extract ticker-scoped quote fields from an ASP / stealth-browser fetch result."""
    flat: dict[str, Any] = {}
    html = result.get("html") or result.get("price_html_excerpt") or ""
    page_text = result.get("page_text") or result.get("text") or ""

    for field, val in parse_fin_streamers_from_html(html, symbol).items():
        flat[f"asp.{field}"] = val

    scoped = parse_price_for_symbol(html or page_text, symbol)
    for key, val in scoped.items():
        flat[f"asp.{key}"] = val

    qsp_m = re.search(r'data-test="qsp-price"[^>]*>([^<]+)<', html or page_text, re.I)
    if qsp_m:
        price = _coerce_price_value(qsp_m.group(1))
        if price:
            flat["asp.regularMarketPrice"] = price

    for idx, candidate in enumerate(result.get("price_candidates_usd") or []):
        flat[f"asp.price_candidate_{idx}"] = candidate

    return flat


async def fetch_yahoo_quote_with_browser_session(ticker: str, browser_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Use a real browser session (cookies + crumb) to call Yahoo quoteSummary.
    This is the reliable path for Yahoo's SPA quote pages.
    """
    from app.services.crawler.browser_fetcher import fetch_stealth_browser

    sym = ticker.upper().strip()
    page_url = f"https://finance.yahoo.com/quote/{sym}/"
    browser = browser_result
    if not browser or browser.get("status") != "ok" or not browser.get("cookies") or not browser.get("html"):
        browser = await fetch_stealth_browser(page_url, retailer_key=YAHOO_RETAILER_KEY)

    meta = {
        "browser_status": browser.get("status"),
        "fetch_backend": browser.get("fetch_backend"),
        "block_reason": browser.get("block_reason"),
    }
    if browser.get("status") != "ok":
        return {"modules": {}, "html": "", "source": "yahoo_session_blocked", "meta": meta}

    html = browser.get("html") or ""
    cookies = browser.get("cookies") or {}
    crumb_m = re.search(r'"CrumbStore"\s*:\s*\{[^}]*"crumb"\s*:\s*"([^"]+)"', html)
    if not crumb_m:
        crumb_m = re.search(r'"crumb"\s*:\s*"([^"]+)"', html)
    crumb = crumb_m.group(1) if crumb_m else ""

    modules: dict[str, Any] = {}
    mod_str = ",".join(QUOTE_MODULES)
    async with httpx.AsyncClient(
        headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
        cookies=cookies,
        timeout=httpx.Timeout(45.0),
    ) as client:
        params: dict[str, str] = {"modules": mod_str}
        if crumb:
            params["crumb"] = crumb
        for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
            try:
                r = await client.get(
                    f"https://{host}/v10/finance/quoteSummary/{sym}",
                    params=params,
                )
            except httpx.RequestError:
                continue
            if r.status_code == 200:
                rows = (r.json().get("quoteSummary") or {}).get("result") or []
                if rows:
                    modules = rows[0]
                    meta["yahoo_api_host"] = host
                    break
            meta["quote_summary_status"] = r.status_code
            if r.status_code != 200:
                meta["quote_summary_error"] = r.text[:200]

    return {
        "modules": modules,
        "html": html,
        "source": "yahoo_browser_session_api" if modules else "yahoo_browser_session_no_api",
        "meta": {**meta, "crumb_found": bool(crumb), "cookie_count": len(cookies)},
    }


_YAHOO_VISION_PROMPT = (
    "Extract ALL visible stock quote data from these Yahoo Finance quote page panels. "
    "Return one merged JSON object with sections:\n"
    "1) quote_header: ticker, company_name, exchange, regularMarketPrice, regularMarketChange, "
    "regularMarketChangePercent, regularMarketVolume, regularMarketDayHigh, regularMarketDayLow, "
    "regularMarketOpen, regularMarketPreviousClose, fiftyTwoWeekHigh, fiftyTwoWeekLow\n"
    "2) summary: marketCap, trailingPE, forwardPE, dividendYield, eps, beta, averageVolume, "
    "averageVolume10days, sharesOutstanding, floatShares\n"
    "3) analyst: targetMeanPrice, targetHighPrice, targetLowPrice, recommendation, "
    "numberOfAnalystOpinions\n"
    "4) profile: sector, industry, fullTimeEmployees, website, longBusinessSummary (truncate if long)\n"
    "5) financial_highlights: totalRevenue, revenuePerShare, grossProfits, ebitda, profitMargins, "
    "operatingMargins, returnOnEquity, returnOnAssets, totalCash, totalDebt, debtToEquity\n"
    "6) holders_or_insiders: any visible holder/insider rows as array of {name, shares, value}\n"
    "Use null for fields not visible in any panel. Do not invent values. Do not extract news headlines."
)


async def fetch_yahoo_via_screenshot(ticker: str) -> dict[str, Any]:
    """
    Last-resort path: scroll Playwright page → multiple viewport PNGs → vision LLM.
    Works when HTML/API miss hydrated SPA content or APIs rate-limit.
    """
    from app.config import get_settings
    from app.services.asp.profiles import get_retailer_profile
    from app.services.crawler.browser_pool import get_browser_pool
    from app.services.crawler.human_behavior import dismiss_consent, run_behavior
    from app.services.crawler.screenshot_capture import capture_scrolled_screenshots
    from llm import extract_from_screenshots

    sym = ticker.upper().strip()
    page_url = f"https://finance.yahoo.com/quote/{sym}/"
    profile = get_retailer_profile(YAHOO_RETAILER_KEY)
    settings = get_settings()
    meta: dict[str, Any] = {"path": "screenshot_vision_scroll"}

    pool = await get_browser_pool(size=settings.browser_pool_size)
    async with pool.page(retailer_key=YAHOO_RETAILER_KEY) as (page, _ctx):
        await page.goto(page_url, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
        await page.wait_for_timeout(1500)
        await dismiss_consent(page, profile.get("consent_selectors", []))
        wait_sel = profile.get("wait_selector")
        if wait_sel:
            try:
                await page.wait_for_selector(wait_sel, timeout=10_000, state="visible")
            except Exception:
                pass
        await page.wait_for_timeout(int(profile.get("hydration_wait_ms") or 6000))
        await run_behavior(page)

        shots = await capture_scrolled_screenshots(page)
        meta["screenshot_count"] = len(shots)
        meta["screenshot_bytes"] = sum(s["bytes"] for s in shots)
        meta["screenshot_scroll_ys"] = [s["scroll_y"] for s in shots]
        images = [s["png"] for s in shots]

    extracted = await extract_from_screenshots(
        images,
        _YAHOO_VISION_PROMPT,
        extra_context=f"Ticker: {sym}. URL: {page_url}. {len(images)} scroll panels.",
    )

    flat: dict[str, Any] = {}
    for k, v in extracted.items():
        if str(k).startswith("_") or k == "news":
            continue
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if sub_v is not None:
                    flat[f"vision.{k}.{sub_k}"] = sub_v
        else:
            flat[f"vision.{k}"] = v

    flat = _strip_news_from_flat(flat)

    # Promote common quote fields to top-level vision.* keys for price detection
    for alias in (
        ("quote_header.regularMarketPrice", "vision.regularMarketPrice"),
        ("regularMarketPrice", "vision.regularMarketPrice"),
        ("quote_header.ticker", "vision.ticker"),
        ("ticker", "vision.ticker"),
    ):
        src, dst = alias
        if dst not in flat:
            if src in flat:
                flat[dst] = flat[src]
            elif "." in src:
                section, field = src.split(".", 1)
                nested = extracted.get(section)
                if isinstance(nested, dict) and nested.get(field) is not None:
                    flat[dst] = nested[field]

    return {
        "flat": flat,
        "source": "yahoo_screenshot_vision" if flat and "_error" not in extracted else "yahoo_screenshot_failed",
        "meta": {**meta, "vision_error": extracted.get("_error")},
    }


async def fetch_yahoo_modules_via_browser_network(ticker: str) -> dict[str, Any]:
    """
    Load the quote page in stealth Playwright and capture Yahoo's own
    quoteSummary / chart JSON responses from the network (most reliable for SPAs).
    """
    from app.config import get_settings
    from app.services.asp.profiles import get_retailer_profile
    from app.services.crawler.browser_pool import get_browser_pool
    from app.services.crawler.human_behavior import dismiss_consent

    sym = ticker.upper().strip()
    page_url = f"https://finance.yahoo.com/quote/{sym}/"
    profile = get_retailer_profile(YAHOO_RETAILER_KEY)
    settings = get_settings()
    modules: dict[str, Any] = {}
    chart_meta: dict[str, Any] = {}
    html = ""

    def _is_quote_summary(resp) -> bool:
        return (
            resp.status == 200
            and f"/{sym}" in resp.url.upper()
            and "quoteSummary" in resp.url
        )

    def _is_chart(resp) -> bool:
        return (
            resp.status == 200
            and f"/{sym}" in resp.url.upper()
            and "/finance/chart/" in resp.url
        )

    pool = await get_browser_pool(size=settings.browser_pool_size)
    async with pool.page(retailer_key=YAHOO_RETAILER_KEY) as (page, _ctx):
        summary_payload: dict[str, Any] | None = None
        chart_payload: dict[str, Any] | None = None

        pending: list[asyncio.Task] = []

        async def _on_response(response) -> None:
            nonlocal summary_payload, chart_payload
            try:
                if summary_payload is None and _is_quote_summary(response):
                    summary_payload = await response.json()
                elif chart_payload is None and _is_chart(response):
                    chart_payload = await response.json()
            except Exception:
                logger.debug("Failed parsing Yahoo network response", exc_info=True)

        def _hook(response) -> None:
            pending.append(asyncio.create_task(_on_response(response)))

        page.on("response", _hook)
        await page.goto(page_url, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
        await page.wait_for_timeout(1500)
        await dismiss_consent(page, profile.get("consent_selectors", []))
        hydration = int(profile.get("hydration_wait_ms") or 4000)
        await page.wait_for_timeout(hydration)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        page.remove_listener("response", _hook)

        if summary_payload:
            rows = (summary_payload.get("quoteSummary") or {}).get("result") or []
            if rows:
                modules = rows[0]
        if chart_payload:
            rows = (chart_payload.get("chart") or {}).get("result") or []
            if rows:
                chart_meta = rows[0].get("meta") or {}

        html = (await page.content())[:350_000]

    return {"modules": modules, "chart_meta": chart_meta, "html": html, "source": "yahoo_browser_network"}


async def fetch_yahoo_via_asp(ticker: str) -> dict[str, Any]:
    """
    Fetch through FinCrawler ASP: browser_grid → impersonate → stealth browser
    → captcha browser → proxy rotation, with stealth-browser fallback.
    """
    from app.services.crawler.browser_fetcher import fetch_stealth_browser
    from app.services.crawler.managed_fetcher import fetch_managed

    sym = ticker.upper().strip()
    page_url = f"https://finance.yahoo.com/quote/{sym}/"
    meta: dict[str, Any] = {"retailer_key": YAHOO_RETAILER_KEY}

    asp_result = await fetch_managed(page_url, retailer_key=YAHOO_RETAILER_KEY)
    meta.update(
        {
            "asp_status": asp_result.get("status"),
            "asp_attempts": asp_result.get("asp_attempts") or [],
            "fetch_backend": asp_result.get("fetch_backend"),
            "tier_used": asp_result.get("tier_used"),
            "tier_name": asp_result.get("tier_name"),
            "block_reason": asp_result.get("block_reason"),
        }
    )

    flat: dict[str, Any] = {}
    html = ""
    modules: dict[str, Any] = {}

    if asp_result.get("status") == "ok":
        flat = extract_quote_from_fetch_result(asp_result, sym)
        html = asp_result.get("html") or ""

    if asp_result.get("status") != "ok" or not _has_quote_price(flat):
        meta["stealth_fallback"] = True
        stealth = await fetch_stealth_browser(page_url, retailer_key=YAHOO_RETAILER_KEY)
        meta["stealth_status"] = stealth.get("status")
        meta["stealth_block_reason"] = stealth.get("block_reason")
        meta["stealth_backend"] = stealth.get("fetch_backend")
        if stealth.get("status") == "ok":
            flat.update(extract_quote_from_fetch_result(stealth, sym))
            html = stealth.get("html") or html
    else:
        stealth = asp_result

    if not modules:
        meta["browser_session_api"] = True
        session = await fetch_yahoo_quote_with_browser_session(sym, browser_result=stealth if stealth.get("status") == "ok" else None)
        meta.update(session.get("meta") or {})
        if session.get("modules"):
            modules = session["modules"]
            html = session.get("html") or html
        elif session.get("html"):
            html = session.get("html") or html

    if not modules:
        api_result = await fetch_quote_summary_http(sym)
        if api_result.get("modules"):
            modules = api_result["modules"]
            meta["yahoo_api_host"] = api_result.get("host")
            meta["yahoo_api_source"] = "quote_summary_http"
    elif meta.get("yahoo_api_host") is None:
        meta["yahoo_api_host"] = meta.get("yahoo_api_host") or "browser_session"

    if not modules:
        meta["browser_network_api"] = True
        try:
            net = await fetch_yahoo_modules_via_browser_network(sym)
            if net.get("modules"):
                modules = net["modules"]
                meta["yahoo_api_source"] = "browser_network"
            chart_meta = net.get("chart_meta") or {}
            if chart_meta.get("regularMarketPrice") and not _has_quote_price(flat):
                flat["chart.regularMarketPrice"] = chart_meta["regularMarketPrice"]
            if net.get("html"):
                html = net.get("html") or html
        except Exception:
            logger.exception("Yahoo browser network capture failed for %s", sym)

    vision_source: str | None = None
    if not _yahoo_api_succeeded(modules):
        flat, vision_source = await _run_vision_fallback(sym, flat, meta)

    if modules:
        source = "yahoo_asp_api"
    elif vision_source:
        source = vision_source
    elif flat:
        source = "yahoo_asp_dom"
    elif meta.get("block_reason") or meta.get("stealth_block_reason"):
        source = "yahoo_asp_blocked"
    else:
        source = "yahoo_asp"

    return {"flat": flat, "modules": modules, "html": html, "meta": meta, "source": source}


async def fetch_quote_summary_browser(ticker: str) -> dict[str, Any]:
    """Route Yahoo quote fetch through the ASP antibot stack."""
    asp = await fetch_yahoo_via_asp(ticker)
    flat = asp.get("flat") or {}
    modules = asp.get("modules") or {}
    if modules:
        return {
            "modules": modules,
            "source": asp.get("source", "yahoo_asp_api"),
            "html": asp.get("html") or "",
            "meta": asp.get("meta"),
        }
    return {
        "source": asp.get("source", "yahoo_asp_dom"),
        "html": asp.get("html") or "",
        "dom_data": flat,
        "meta": asp.get("meta"),
    }


async def fetch_yahoo_news_rss(ticker: str, limit: int = 15) -> list[dict[str, str]]:
    sym = ticker.upper().strip()
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
    articles: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return articles
            for m in re.finditer(r"<item>([\s\S]*?)</item>", r.text, re.I):
                block = m.group(1)
                title_m = re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</title>", block, re.I)
                link_m = re.search(r"<link>([\s\S]*?)</link>", block, re.I)
                desc_m = re.search(r"<description>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</description>", block, re.I)
                if not title_m:
                    continue
                title = re.sub(r"\s+", " ", title_m.group(1)).strip()
                articles.append(
                    {
                        "title": title,
                        "link": (link_m.group(1).strip() if link_m else ""),
                        "summary": re.sub(r"<[^>]+>", " ", desc_m.group(1)).strip()[:500] if desc_m else "",
                        "publisher": "Yahoo Finance",
                    }
                )
                if len(articles) >= limit:
                    break
    except Exception:
        logger.exception("Yahoo RSS fetch failed for %s", sym)
    return articles


def parse_news_articles_from_html(html: str, limit: int = 15) -> list[dict[str, str]]:
    """Parse news headline links from a Yahoo Finance news page HTML shell."""
    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href="([^"]+)"[^>]*>[\s\S]{0,200}?<h3[^>]*>([^<]{8,240})</h3>',
        html,
        re.I,
    ):
        link, title = m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip()
        if title in seen:
            continue
        seen.add(title)
        articles.append(
            {
                "title": title,
                "summary": "",
                "link": link,
                "publisher": "Yahoo Finance",
            }
        )
        if len(articles) >= limit:
            break
    if articles:
        return articles

    for m in re.finditer(r"<h3[^>]*>([^<]{12,240})</h3>", html, re.I):
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title in seen:
            continue
        seen.add(title)
        articles.append(
            {
                "title": title,
                "summary": "",
                "link": "",
                "publisher": "Yahoo Finance",
            }
        )
        if len(articles) >= limit:
            break
    return articles


async def fetch_yahoo_news(
    ticker: str,
    *,
    limit: int = 15,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Fetch Yahoo Finance news for a ticker — separate from quote/data fetch
    so callers can run quote + news in parallel.
    """
    from cache import cache

    sym = ticker.upper().strip()
    if not sym:
        return {"ok": False, "ticker": sym, "error": "ticker_required", "articles": [], "count": 0}

    limit = max(1, min(limit, 25))
    cache_key = f"yahoo:news:{sym}:{limit}"
    if not force_refresh:
        cached = await cache.get(cache_key)
        if cached:
            cached["cache_hit"] = True
            return cached

    articles = await fetch_yahoo_news_rss(sym, limit=limit)
    source = "yahoo_rss"

    if not articles:
        from app.services.crawler.compliant_fetcher import fetch_compliant

        page_url = f"https://finance.yahoo.com/quote/{sym}/news"
        crawl = await fetch_compliant(page_url)
        html = crawl.get("html") or crawl.get("text") or ""
        if html:
            articles = parse_news_articles_from_html(html, limit=limit)
            if articles:
                source = "yahoo_html"

    result: dict[str, Any] = {
        "ok": bool(articles),
        "ticker": sym,
        "url": f"https://finance.yahoo.com/quote/{sym}/news",
        "articles": articles,
        "count": len(articles),
        "source": source,
        "cache_hit": False,
    }
    if result["ok"]:
        await cache.set(cache_key, result)
    else:
        result["error"] = "yahoo_news_not_found"
    return result


async def fetch_yahoo_full(
    ticker: str,
    *,
    force_refresh: bool = False,
    include_html: bool = False,
) -> dict[str, Any]:
    """
    Fetch Yahoo Finance quote data for *ticker* (quote only — no news).

    Use ``fetch_yahoo_news()`` separately for headlines.
    """
    from cache import cache

    sym = ticker.upper().strip()
    if not sym:
        return {"ok": False, "ticker": sym, "error": "ticker_required"}

    cache_key = f"yahoo:full:{sym}"
    if not force_refresh:
        cached = await cache.get(cache_key)
        if cached:
            cached["cache_hit"] = True
            if cached.get("data"):
                cached["data"] = _strip_news_from_flat(cached["data"])
            cached.pop("news", None)
            cached.pop("news_count", None)
            return cached

    asp = await fetch_yahoo_via_asp(sym)
    modules = asp.get("modules") or {}
    flat: dict[str, Any] = dict(asp.get("flat") or {})
    html = asp.get("html") or ""
    source = asp.get("source") or "yahoo_asp"
    asp_meta = asp.get("meta") or {}
    attempts = ["asp"] + list(asp_meta.get("asp_attempts") or [])

    if modules:
        flat.update(flatten_yahoo_modules({k: v for k, v in modules.items() if k != "symbol"}))
        attempts.append("yahoo_api")
    elif asp_meta.get("vision_fallback"):
        attempts.append("vision_screenshot")

    if not _has_quote_price(flat) and not asp_meta.get("vision_fallback"):
        from app.services.crawler.compliant_fetcher import fetch_compliant

        if not html:
            crawl = await fetch_compliant(f"https://finance.yahoo.com/quote/{sym}/")
            html = crawl.get("html") or ""
        scoped = parse_price_for_symbol(html, sym)
        if scoped:
            flat.update({f"html.{k}": v for k, v in scoped.items()})
            source = source or "yahoo_html"
            attempts.append("html_parse")

    # Safety net: API failed, vision was skipped/disabled, still no price
    if not _yahoo_api_succeeded(modules) and not asp_meta.get("vision_fallback_ok"):
        flat, vision_source = await _run_vision_fallback(sym, flat, asp_meta)
        if vision_source:
            source = vision_source
            attempts.append("vision_screenshot")
        asp_meta = {**asp_meta}

    module_names = [k for k in modules.keys() if k != "symbol"] if modules else []
    flat = _strip_news_from_flat(flat)
    result: dict[str, Any] = {
        "ok": bool(flat),
        "ticker": sym,
        "url": f"https://finance.yahoo.com/quote/{sym}/",
        "source": source or "none",
        "cache_hit": False,
        "attempts": attempts,
        "asp_meta": asp_meta,
        "modules_fetched": module_names,
        "field_count": len(flat),
        "data": flat,
    }
    if include_html and html:
        result["html"] = html[:350_000]

    if modules:
        result["modules"] = {
            k: v for k, v in modules.items() if k in module_names
        }

    if result["ok"]:
        await cache.set(cache_key, result)

    if not result["ok"]:
        result["error"] = "yahoo_data_not_found"

    return result
