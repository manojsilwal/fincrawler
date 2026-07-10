"""Yahoo Finance JSON API via curl_cffi browser TLS impersonation."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_IMPERSONATE = os.getenv("YAHOO_CURL_IMPERSONATE", "chrome124")
_QUOTE_MODULES = (
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


def _impersonate_target() -> str:
    return os.getenv("YAHOO_CURL_IMPERSONATE", _DEFAULT_IMPERSONATE).strip() or "chrome124"


def _extract_crumb(html: str) -> str | None:
    if not html:
        return None
    for pat in (
        r'"CrumbStore"\s*:\s*\{[^}]*"crumb"\s*:\s*"([^"]+)"',
        r'"crumb"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


async def yahoo_curl_session_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    impersonate: str | None = None,
    proxy: str | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Single GET via curl_cffi AsyncSession."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return {"ok": False, "error": "curl_cffi_not_installed", "status": 0}

    imp = impersonate or _impersonate_target()
    try:
        async with AsyncSession(impersonate=imp) as session:
            r = await session.get(
                url,
                params=params,
                timeout=timeout,
                proxy=proxy or None,
                allow_redirects=True,
            )
            text = r.text or ""
            try:
                body = r.json()
            except Exception:
                body = None
            return {
                "ok": r.status_code == 200,
                "status": r.status_code,
                "url": str(r.url),
                "text": text,
                "json": body,
                "impersonate": imp,
            }
    except Exception as exc:
        logger.debug("curl_cffi GET failed for %s", url, exc_info=True)
        return {"ok": False, "error": str(exc), "status": 0, "impersonate": imp}


async def fetch_yahoo_quote_summary_curl(
    ticker: str,
    *,
    modules: tuple[str, ...] = _QUOTE_MODULES,
    impersonate: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """
    Yahoo quoteSummary using curl_cffi session (warm quote page → crumb → API).

    Mimics a real browser TLS/HTTP2 fingerprint to reduce 429 rate limits.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return {"source": "yahoo_curl_unavailable"}

    sym = ticker.upper().strip()
    if not sym:
        return {}

    imp = impersonate or _impersonate_target()
    page_url = f"https://finance.yahoo.com/quote/{sym}/"
    mod_str = ",".join(modules)

    try:
        async with AsyncSession(impersonate=imp) as session:
            warm = await session.get(
                "https://finance.yahoo.com/",
                timeout=30,
                proxy=proxy or None,
                allow_redirects=True,
            )
            quote = await session.get(
                page_url,
                timeout=45,
                proxy=proxy or None,
                allow_redirects=True,
            )
            html = quote.text or ""
            crumb = _extract_crumb(html)
            if not crumb:
                crumb_resp = await session.get(
                    "https://query1.finance.yahoo.com/v1/test/getcrumb",
                    timeout=20,
                    proxy=proxy or None,
                )
                if crumb_resp.status_code == 200 and crumb_resp.text:
                    crumb = crumb_resp.text.strip()

            params: dict[str, str] = {"modules": mod_str}
            if crumb:
                params["crumb"] = crumb

            meta = {
                "impersonate": imp,
                "warm_status": warm.status_code,
                "quote_status": quote.status_code,
                "crumb_found": bool(crumb),
                "cookie_count": len(getattr(session, "cookies", {}) or {}),
            }

            for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
                api_url = f"https://{host}/v10/finance/quoteSummary/{sym}"
                r = await session.get(
                    api_url,
                    params=params,
                    timeout=45,
                    proxy=proxy or None,
                )
                meta[f"{host}_status"] = r.status_code
                if r.status_code == 200:
                    payload = r.json()
                    rows = (payload.get("quoteSummary") or {}).get("result") or []
                    if rows:
                        return {
                            "modules": rows[0],
                            "source": "yahoo_curl_api",
                            "host": host,
                            "meta": meta,
                        }
                logger.info(
                    "curl_cffi quoteSummary %s returned %s for %s",
                    host,
                    r.status_code,
                    sym,
                )
            return {"meta": meta, "source": "yahoo_curl_failed"}
    except Exception as exc:
        logger.warning("curl_cffi Yahoo session failed for %s: %s", sym, exc)
        return {"source": "yahoo_curl_error", "error": str(exc)}


async def fetch_yahoo_chart_curl(
    ticker: str,
    *,
    impersonate: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Yahoo chart API via curl_cffi (same session pattern as quoteSummary)."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return {"flat": {}, "source": "yahoo_curl_unavailable"}

    sym = ticker.upper().strip()
    imp = impersonate or _impersonate_target()
    flat: dict[str, Any] = {}
    page_url = f"https://finance.yahoo.com/quote/{sym}/"

    try:
        async with AsyncSession(impersonate=imp) as session:
            await session.get("https://finance.yahoo.com/", timeout=30, proxy=proxy or None)
            await session.get(page_url, timeout=45, proxy=proxy or None)
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = await session.get(
                url,
                params={"interval": "1d", "range": "1d"},
                timeout=45,
                proxy=proxy or None,
            )
            if r.status_code != 200:
                return {
                    "flat": flat,
                    "source": "yahoo_curl_chart_failed",
                    "status": r.status_code,
                    "impersonate": imp,
                }
            rows = (r.json().get("chart") or {}).get("result") or []
            if not rows:
                return {"flat": flat, "source": "yahoo_curl_chart_empty", "impersonate": imp}
            meta = rows[0].get("meta") or {}
            price = meta.get("regularMarketPrice")
            if price is not None:
                flat["chart.regularMarketPrice"] = price
                flat["chart.symbol"] = sym
            if meta.get("currency"):
                flat["chart.currency"] = meta["currency"]
            if meta.get("shortName"):
                flat["chart.shortName"] = meta["shortName"]
            return {"flat": flat, "source": "yahoo_curl_chart", "impersonate": imp}
    except Exception as exc:
        logger.debug("curl_cffi chart failed for %s", sym, exc_info=True)
        return {"flat": flat, "source": "yahoo_curl_chart_error", "error": str(exc)}


async def verify_tls_fingerprint(impersonate: str | None = None) -> dict[str, Any]:
    """Hit tls.peet.ws to confirm curl_cffi impersonation is active."""
    result = await yahoo_curl_session_get(
        "https://tls.peet.ws/api/all",
        impersonate=impersonate,
    )
    if not result.get("ok"):
        return result
    data = result.get("json") or {}
    return {
        "ok": True,
        "impersonate": result.get("impersonate"),
        "ja3_hash": data.get("ja3_hash"),
        "user_agent": data.get("user_agent") or data.get("headers", {}).get("user-agent"),
        "tls_version": data.get("tls_version"),
    }
