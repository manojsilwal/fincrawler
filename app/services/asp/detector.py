"""Block detection and product-list validation for ASP scrapes."""

from __future__ import annotations

from app.services.asp.profiles import get_retailer_profile


def has_product_markers(html: str, profile: dict) -> bool:
    if not profile:
        return True
    blob = (html or "")[:120_000].lower()
    markers = profile.get("product_markers") or []
    return any(m.lower() in blob for m in markers)


def is_usable_scrape(result: dict, retailer_key: str = "") -> bool:
    if result.get("status") != "ok":
        return False
    # Grid workers validate product markers against full HTML before returning ok,
    # but strip HTML from the Redis payload — trust their ok status.
    if result.get("fetch_backend") == "browser_grid":
        return True
    html = result.get("html") or ""
    visible = result.get("page_text") or result.get("text") or ""
    profile = get_retailer_profile(retailer_key) if retailer_key else {}
    if has_product_markers(html, profile) or has_product_markers(visible, profile):
        return True
    if result.get("fetch_backend") in ("brightdata_scraping_browser", "browser_grid"):
        chars = result.get("char_count") or len(visible) or len(html)
        title = (result.get("title") or "").lower()
        min_chars = int(profile.get("min_usable_chars", 12_000))
        if chars >= min_chars and not any(
            x in title for x in ("error page", "something went wrong", "robot or human", "access denied")
        ):
            return True
    return False
