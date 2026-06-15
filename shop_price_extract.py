"""
Heuristic price extraction and LLM context prep for retail search pages.

Walmart/eBay often embed prices in JSON blobs or noisy markup that small LLMs
miss when given the full page text. We pre-scan HTML, trim context to price-
relevant chunks, and backfill when the LLM returns null.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Reasonable USD bounds for consumer electronics (exclude $0.99 accessories)
_MIN_PRODUCT_USD = 25.0
_MAX_PRODUCT_USD = 45_000.0

# Years mistaken as prices in JSON metadata
_YEAR_MIN = 1990
_YEAR_MAX = 2035

# Chunking for LLM context trimming
_CHARS_PER_TOKEN = 4
_CHUNK_SIZE = 3_000 * _CHARS_PER_TOKEN
_OVERLAP_SIZE = 200 * _CHARS_PER_TOKEN
_TOP_K_CHUNKS = 4


def _chunk_text(text: str) -> list[str]:
    if len(text) <= _CHUNK_SIZE:
        return [text]
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > _CHUNK_SIZE:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(para), _CHUNK_SIZE - _OVERLAP_SIZE):
                chunks.append(para[i : i + _CHUNK_SIZE])
            continue
        if len(current) + len(para) + 2 > _CHUNK_SIZE:
            if current.strip():
                chunks.append(current.strip())
            current = current[-_OVERLAP_SIZE:] + "\n\n" + para if current else para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _score_chunk(chunk: str, query: str) -> float:
    query_terms = set(re.findall(r"\w+", query.lower()))
    chunk_words = re.findall(r"\w+", chunk.lower())
    if not chunk_words:
        return 0.0
    hits = sum(1 for w in chunk_words if w in query_terms)
    return hits / (len(chunk_words) ** 0.5)


def _select_top_chunks(chunks: list[str], query: str, k: int = _TOP_K_CHUNKS) -> list[str]:
    if len(chunks) <= k:
        return chunks
    scored = [(i, _score_chunk(c, query)) for i, c in enumerate(chunks)]
    top_indices = sorted(
        sorted(scored, key=lambda x: x[1], reverse=True)[:k],
        key=lambda x: x[0],
    )
    return [chunks[i] for i, _ in top_indices]


def _is_plausible_product_price(val: float) -> bool:
    if val == int(val) and _YEAR_MIN <= val <= _YEAR_MAX:
        return False
    return _MIN_PRODUCT_USD <= val <= _MAX_PRODUCT_USD


def _add_price(found: set[float], raw: str) -> None:
    try:
        val = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return
    if _is_plausible_product_price(val):
        found.add(round(val, 2))


def extract_prices_from_html(html: str, retailer_key: str | None = None) -> list[float]:
    """Pull USD prices from HTML/JSON patterns (retailer-aware extras)."""
    if not html:
        return []

    found: set[float] = set()
    blob = html[:500_000]

    for m in re.finditer(
        r'"(?:price|currentPrice|listPrice|minPrice|maxPrice|priceDisplay|'
        r'primaryOfferPrice|buyboxPrice|wasPrice|unitPrice|offerPrice)"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        blob,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))

    for m in re.finditer(
        r'itemprop=["\']price["\'][^>]*content=["\']([\d.]+)["\']',
        blob,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))

    for m in re.finditer(
        r'data-(?:price|current-price|strikethrough-price|automation-id)=["\']([\d.]+)["\']',
        blob,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))

    for m in re.finditer(
        r'aria-label=["\']\s*(?:Now\s+)?\$(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)',
        blob,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))

    for m in re.finditer(
        r'class="[^"]*(?:price|currency|amount)[^"]*"[^>]*>\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
        blob,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))

    if retailer_key == "walmart":
        for m in re.finditer(
            r'data-automation-id=["\'](?:product-price|buybox-price)["\'][^>]*>\s*\$?\s*(\d[\d,]*(?:\.\d{2})?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        for m in re.finditer(
            r'"currentPrice"\s*:\s*\{\s*"price"\s*:\s*(\d+(?:\.\d+)?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))

    if retailer_key == "ebay":
        for m in re.finditer(
            r'class="[^"]*s-item__price[^"]*"[^>]*>\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        for m in re.finditer(
            r'"price"\s*:\s*"?\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)"?',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))

    return sorted(found)[:20]


def extract_prices_from_visible_text(text: str) -> list[float]:
    """Extract $ prices from visible page text."""
    if not text:
        return []
    found: set[float] = set()
    for m in re.finditer(
        r'(?:Now\s+|Current\s+price\s+)?\$\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
        text,
        re.IGNORECASE,
    ):
        _add_price(found, m.group(1))
    return sorted(found)[:20]


def pick_best_price(
    candidates: list[float],
    *,
    query: str,
    retailer_key: str | None = None,
) -> Optional[float]:
    """
    Choose the most likely main-product price from regex candidates.
    Prefer prices that appear multiple times; for search pages use the
    lowest plausible price (excludes obvious accessory outliers).
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Drop very low outliers (cases, cables) when higher prices exist
    high = [p for p in candidates if p >= 80]
    pool = high if high else candidates

    # Walmart/eBay search: lowest matching listing is usually the device
    if retailer_key in ("walmart", "ebay", "amazon", "target", "bestbuy"):
        return min(pool)

    return pool[len(pool) // 2]


def prepare_llm_context(
    page_text: str,
    html: str,
    query: str,
    retailer_key: str,
) -> tuple[str, list[float]]:
    """
    Build a focused LLM context string and pre-extracted price candidates.
    """
    html_prices = extract_prices_from_html(html, retailer_key)
    text_prices = extract_prices_from_visible_text(page_text)
    candidates = sorted(set(html_prices + text_prices))[:20]

    retrieval_query = (
        f"{query} price USD current now buy product "
        f"{retailer_key} dollars sale discount"
    )
    chunks = _chunk_text(page_text or "")
    if chunks:
        relevant = _select_top_chunks(chunks, query=retrieval_query, k=4)
        context = "\n\n---\n\n".join(relevant)
    else:
        context = page_text[:48_000]

    if candidates:
        hint = ", ".join(f"${p:.2f}" for p in candidates[:8])
        context = (
            f"Pre-detected USD price candidates on this page: {hint}\n"
            f"Use one of these for the main product if it matches '{query}'.\n\n"
            + context
        )

    return context[:52_000], candidates


_RETAILER_PROMPT_HINTS: dict[str, str] = {
    "walmart": (
        "Walmart search page: find the main product listing (not accessories). "
        "Price often appears as 'Now $X.XX', in data-automation-id product-price, "
        "or embedded JSON currentPrice. Return the current selling price in USD."
    ),
    "ebay": (
        "eBay search results: use Buy It Now or fixed price (class s-item__price). "
        "Ignore auction starting bids under $5 and accessory listings. "
        "Pick the best-matching listing for the exact product."
    ),
    "amazon": (
        "Amazon search: use the main search result price (not sponsored accessories). "
        "Look for price whole/fraction or 'priceToPay' patterns."
    ),
    "bestbuy": (
        "Best Buy search: use priceView-hero-price or current sale price for the device."
    ),
    "target": (
        "Target search: use current price on the primary product card, not add-ons."
    ),
}


def retailer_prompt_hint(retailer_key: str) -> str:
    return _RETAILER_PROMPT_HINTS.get(retailer_key, "")


def merge_shop_extraction(
    llm_data: dict[str, Any],
    candidates: list[float],
    *,
    query: str,
    retailer_key: str,
) -> dict[str, Any]:
    """
    Backfill missing LLM price from regex candidates; annotate price_source.
    """
    data = dict(llm_data) if llm_data else {}
    if "_error" in data and data.get("_error") not in ("product_not_found",):
        return data

    price = data.get("price")
    if price is not None:
        try:
            price = float(price)
            if price > 0:
                data["price"] = round(price, 2)
                data.setdefault("price_source", "llm")
                if "_error" in data:
                    del data["_error"]
                return data
        except (TypeError, ValueError):
            data["price"] = None

    fallback = pick_best_price(candidates, query=query, retailer_key=retailer_key)
    if fallback is not None:
        data["price"] = fallback
        data["price_source"] = "regex"
        data["price_candidates_usd"] = candidates
        if data.get("_error") == "product_not_found":
            del data["_error"]
        return data

    if candidates:
        data["price_candidates_usd"] = candidates
    return data


def crawl_likely_blocked(crawl: dict) -> bool:
    """Detect bot-wall pages that still return HTTP 200."""
    url = (crawl.get("url") or "").lower()
    if "/blocked" in url or "challenge" in url:
        return True

    title = (crawl.get("title") or "").lower()
    if any(n in title for n in ("robot", "blocked", "access denied", "captcha")):
        return True

    text = (crawl.get("page_text") or crawl.get("text") or "")[:12_000]
    blob = text.lower()
    needles = (
        "robot or human",
        "robot check",
        "verify you are human",
        "unusual traffic",
        "access denied",
        "px-captcha",
        "please enable javascript",
        "automated access",
    )
    if any(n in blob for n in needles):
        return True

    # Very short body after a retailer search usually means a challenge interstitial
    char_count = crawl.get("char_count") or len(text)
    if char_count < 2500 and any(n in blob for n in ("captcha", "security", "sign in")):
        return True

    return False


def extract_google_listings_from_page(
    page_text: str,
    html: str,
    query: str,
) -> list[dict]:
    """
    Regex fallback for Google Shopping when the LLM returns empty listings.
    """
    retailer_patterns: dict[str, list[str]] = {
        "amazon": [r"\bamazon(?:\.com)?\b"],
        "walmart": [r"\bwalmart(?:\.com)?\b"],
        "ebay": [r"\bebay(?:\.com)?\b"],
        "bestbuy": [r"\bbest\s*buy(?:\.com)?\b", r"\bbestbuy(?:\.com)?\b"],
        "target": [r"\btarget(?:\.com)?\b"],
    }

    found: dict[str, dict] = {}

    def _record(key: str, price: float, source: str) -> None:
        if price is None:
            return
        existing = found.get(key)
        if existing is None or price < existing["price"]:
            found[key] = {
                "retailer": key.replace("bestbuy", "Best Buy").title().replace("Best Buy", "Best Buy"),
                "retailer_key": key,
                "price": price,
                "price_source": source,
                "product_name": query,
            }

    # aria-label blobs often contain merchant + price
    for blob in (html or "", page_text or ""):
        for m in re.finditer(r'aria-label=["\']([^"\']{8,240})["\']', blob, re.IGNORECASE):
            label = m.group(1)
            label_lower = label.lower()
            prices = extract_prices_from_visible_text(label)
            if not prices:
                continue
            for key, patterns in retailer_patterns.items():
                if any(re.search(p, label_lower) for p in patterns):
                    price = pick_best_price(prices, query=query, retailer_key=key)
                    _record(key, price, "regex_aria")

    lines = (page_text or "").splitlines()
    for i, line in enumerate(lines):
        lower = line.lower()
        window = "\n".join(lines[max(0, i - 1) : i + 4])
        prices = extract_prices_from_visible_text(window)
        if not prices:
            continue
        for key, patterns in retailer_patterns.items():
            if any(re.search(p, lower) for p in patterns):
                price = pick_best_price(prices, query=query, retailer_key=key)
                _record(key, price, "regex_text")

    listings = list(found.values())
    listings.sort(key=lambda x: x.get("price") or float("inf"))
    return listings


def shop_result_missing_price(result: dict) -> bool:
    """True when crawl succeeded but we have no usable price."""
    if result.get("status") not in ("ok", "ok_via_google"):
        return False
    data = result.get("data")
    if not isinstance(data, dict):
        return True
    if data.get("_error") in ("product_not_found", "product_name_mismatch", "json_parse_failed"):
        return True
    price = data.get("price")
    if price is None:
        return True
    try:
        return float(price) <= 0
    except (TypeError, ValueError):
        return True
