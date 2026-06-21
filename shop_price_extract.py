"""
Heuristic price extraction and LLM context prep for retail search pages.

Walmart/eBay often embed prices in JSON blobs or noisy markup that small LLMs
miss when given the full page text. We pre-scan HTML, trim context to price-
relevant chunks, and backfill when the LLM returns null.
"""

from __future__ import annotations

import json
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


def _sanitize_page_text(text: str) -> str:
    """Drop CSS-heavy blobs that poison LLM extraction (common on eBay SPAs)."""
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith(":root") or stripped.startswith("@charset") or stripped.startswith("{"):
        # Prefer lines that look like visible content
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        content_lines = [
            ln for ln in lines
            if not ln.startswith((":", "@", "{", "}", "--"))
            and len(ln) > 3
            and not re.match(r"^[\s\{\};:@#.\-0-9%]+$", ln)
        ]
        if content_lines:
            return "\n".join(content_lines)[:350_000]
        return ""
    return text


def _strip_html_noise(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)


def extract_prices_from_html(html: str, retailer_key: str | None = None) -> list[float]:
    """Pull USD prices from HTML/JSON patterns (retailer-aware extras)."""
    if not html:
        return []

    found: set[float] = set()
    blob = _strip_html_noise(html)[:500_000]

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
        for m in re.finditer(
            r'"currentPrice"\s*:\s*(\d+(?:\.\d+)?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        for m in re.finditer(
            r'"price"\s*:\s*(\d+(?:\.\d+)?)\s*,\s*"currency"\s*:\s*"USD"',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        for m in re.finditer(
            r'"priceInfo"\s*:\s*\{[^}]*"currentPrice"\s*:\s*\{[^}]*"price"\s*:\s*(\d+(?:\.\d+)?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        nd_m = re.search(
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(\{.*?\})</script>',
            blob,
            re.I | re.S,
        )
        if nd_m:
            try:
                _walk_walmart_prices(json.loads(nd_m.group(1)), found)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    if retailer_key == "ebay":
        for pattern in (
            r'class="[^"]*s-item__price[^"]*"[^>]*>\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
            r'class="[^"]*s-card__price[^"]*"[^>]*>\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
            r'class="[^"]*default__price[^"]*"[^>]*>\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)',
            r'itemprop=["\']price["\'][^>]*content=["\']([\d.]+)["\']',
            r'"price"\s*:\s*"?\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)"?',
            r'"displayPrice"\s*:\s*"?\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)"?',
            r'"value"\s*:\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)\s*,\s*"currency"\s*:\s*"USD"',
        ):
            for m in re.finditer(pattern, blob, re.IGNORECASE):
                _add_price(found, m.group(1))

    if retailer_key == "bestbuy":
        for pattern in (
            r'"customerPrice"\s*:\s*(\d+(?:\.\d+)?)',
            r'"currentPrice"\s*:\s*(\d+(?:\.\d+)?)',
            r'"salePrice"\s*:\s*(\d+(?:\.\d+)?)',
            r'"regularPrice"\s*:\s*(\d+(?:\.\d+)?)',
            r'class="[^"]*priceView-hero-price[^"]*"[^>]*>\s*\$?\s*(\d[\d,]*(?:\.\d{2})?)',
        ):
            for m in re.finditer(pattern, blob, re.IGNORECASE):
                _add_price(found, m.group(1))

    if retailer_key == "target":
        for m in re.finditer(
            r'data-test=["\']current-price["\'][^>]*>\s*\$?\s*(\d[\d,]*(?:\.\d{2})?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))
        for m in re.finditer(
            r'"current_retail(?:_min)?"\s*:\s*(\d+(?:\.\d+)?)',
            blob,
            re.IGNORECASE,
        ):
            _add_price(found, m.group(1))

    return sorted(found)[:20]


def _walk_walmart_prices(node, found: set[float], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, dict):
        cp = node.get("currentPrice")
        if isinstance(cp, dict) and "price" in cp:
            _add_price(found, str(cp["price"]))
        elif isinstance(cp, (int, float)):
            _add_price(found, str(cp))
        price = node.get("price")
        if isinstance(price, (int, float)):
            _add_price(found, str(price))
        for v in node.values():
            _walk_walmart_prices(v, found, depth + 1)
    elif isinstance(node, list):
        for item in node[:40]:
            _walk_walmart_prices(item, found, depth + 1)


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


def _query_price_floor(query: str) -> float:
    """Minimum plausible USD price for the searched product (filters accessory noise)."""
    q = query.lower()
    if re.search(r"osmo|pocket\s*[34]|action\s*cam", q):
        return 280.0
    if re.search(r"\b(iphone|macbook|ipad\s*pro|playstation|xbox|gpu|rtx|drone|gimbal)\b", q):
        return 120.0
    return 25.0


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

    floor = _query_price_floor(query)
    filtered = [p for p in candidates if p >= floor]
    pool = filtered if filtered else candidates

    if len(pool) == 1:
        return pool[0]

    # Drop very low outliers when higher prices exist
    high = [p for p in pool if p >= max(floor, 80)]
    pool = high if high else pool

    if len(pool) >= 2:
        lo, hi = min(pool), max(pool)
        if hi / max(lo, 1) >= 3.0:
            sorted_p = sorted(pool)
            pivot = sorted_p[max(0, len(sorted_p) // 2)]
            cluster = [p for p in pool if p >= pivot * 0.75]
            if cluster:
                pool = cluster

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
    page_text = _sanitize_page_text(page_text)
    html_prices = extract_prices_from_html(_strip_html_noise(html), retailer_key)
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
        "eBay search results: use Buy It Now or fixed price (s-item__price or s-card__price). "
        "Ignore auction starting bids under $5 and accessory listings. "
        "Pick the best-matching listing for the exact product. Price must be a number."
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


def _coerce_listing_price(val: Any) -> float | None:
    if val is None:
        return None
    try:
        price = float(str(val).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    if not _is_plausible_product_price(price):
        return None
    return round(price, 2)


def normalize_listing_item(item: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Normalize one shop listing dict from LLM/vision/HTML."""
    if not isinstance(item, dict):
        return None
    name = (item.get("product_name") or item.get("title") or item.get("name") or "").strip()
    if name.startswith((":root", "@charset", "{", "--")) or "border-width" in name:
        return None
    price = _coerce_listing_price(item.get("price"))
    if not name or price is None:
        return None
    floor = _query_price_floor(query)
    if price < floor:
        return None
    original = _coerce_listing_price(item.get("original_price") or item.get("list_price"))
    out: dict[str, Any] = {
        "product_name": name,
        "price": price,
        "product_url": item.get("product_url") or item.get("url"),
        "seller": item.get("seller"),
        "availability": item.get("availability"),
        "rating": item.get("rating"),
        "review_count": item.get("review_count"),
    }
    if original is not None and original > price:
        out["original_price"] = original
    return {k: v for k, v in out.items() if v is not None}


def dedupe_sort_products(products: list[dict[str, Any]], query: str, max_items: int = 10) -> list[dict[str, Any]]:
    seen: set[tuple[str, float, str]] = set()
    sorted_items = sorted(products, key=lambda p: float(p.get("price") or 999_999.0))
    out: list[dict[str, Any]] = []
    for item in sorted_items:
        seller = str(item.get("seller") or "").lower()
        key = (str(item.get("product_name") or "").lower()[:80], float(item["price"]), seller)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def normalize_shop_data(data: dict[str, Any], query: str, *, max_products: int = 10) -> dict[str, Any]:
    """
    Normalize single or multi-product shop payloads.
    Sets product_name/price from the lowest valid listing for backward compatibility.
    """
    data = dict(data or {})
    products: list[dict[str, Any]] = []
    raw_products = data.get("products")
    if isinstance(raw_products, list) and raw_products:
        for item in raw_products:
            norm = normalize_listing_item(item, query)
            if norm:
                products.append(norm)
    elif data.get("price") or data.get("product_name") or data.get("title"):
        norm = normalize_listing_item(data, query)
        if norm:
            products = [norm]

    products = dedupe_sort_products(products, query, max_products)
    if products:
        best = products[0]
        data["products"] = products
        data["product_name"] = best["product_name"]
        data["price"] = best["price"]
        if best.get("original_price") is not None:
            data["original_price"] = best["original_price"]
        if best.get("seller"):
            data["seller"] = best["seller"]
        if best.get("product_url"):
            data["product_url"] = best["product_url"]
        if data.get("_error") == "product_not_found":
            del data["_error"]
    return data


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
    if isinstance(data.get("products"), list) and data["products"]:
        normalized = normalize_shop_data(data, query)
        if normalized.get("products"):
            normalized.setdefault("price_source", "llm")
            return normalized
    name = (data.get("product_name") or data.get("title") or "").strip()
    if name.startswith((":root", "@charset", "{", "--")) or "border-width" in name:
        data.pop("product_name", None)
        data.pop("title", None)

    if "_error" in data and data.get("_error") not in ("product_not_found",):
        return data

    price = data.get("price")
    if price is not None:
        try:
            price = float(price)
            floor = _query_price_floor(query)
            if price >= floor:
                data["price"] = round(price, 2)
                data.setdefault("price_source", "llm")
                if "_error" in data:
                    del data["_error"]
                return data
            data["price"] = None
        except (TypeError, ValueError):
            data["price"] = None

    fallback = pick_best_price(candidates, query=query, retailer_key=retailer_key)
    floor = _query_price_floor(query)
    if fallback is not None and fallback >= floor:
        data["price"] = fallback
        data["price_source"] = "regex"
        data["price_candidates_usd"] = candidates
        if data.get("_error") == "product_not_found":
            del data["_error"]
        return data

    if candidates:
        data["price_candidates_usd"] = candidates
    return normalize_shop_data(data, query)


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
    retailer_key = crawl.get("retailer_key", "")
    if retailer_key in ("ebay", "walmart", "amazon", "target", "bestbuy") and char_count < 2000:
        return True
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

    def _record(key: str, price: float | None, source: str) -> None:
        if price is None:
            return
        existing = found.get(key)
        if existing is None or price < existing["price"]:
            display = {
                "amazon": "Amazon",
                "walmart": "Walmart",
                "ebay": "eBay",
                "bestbuy": "Best Buy",
                "target": "Target",
            }.get(key, key.title())
            found[key] = {
                "retailer": display,
                "retailer_key": key,
                "price": price,
                "price_source": source,
                "product_name": query,
            }

    def _scan_blob(blob: str, source: str) -> None:
        if not blob:
            return
        # Embedded JSON blobs on Google Shopping pages
        for m in re.finditer(
            r'"(?:merchant|seller|store|retailer)"\s*:\s*"([^"]{2,40})".{0,300}?'
            r'"(?:price|extracted_price|price_str)"\s*:\s*"?(\$?\d[\d,]*(?:\.\d{2})?)"?',
            blob,
            re.IGNORECASE | re.DOTALL,
        ):
            merchant = m.group(1)
            prices = extract_prices_from_visible_text(m.group(2))
            if not prices:
                continue
            for key, patterns in retailer_patterns.items():
                if any(re.search(p, merchant, re.IGNORECASE) for p in patterns):
                    _record(key, pick_best_price(prices, query=query, retailer_key=key), source)

        # aria-label blobs often contain merchant + price
        for m in re.finditer(r'aria-label=["\']([^"\']{8,240})["\']', blob, re.IGNORECASE):
            label = m.group(1)
            label_lower = label.lower()
            prices = extract_prices_from_visible_text(label)
            if not prices:
                continue
            for key, patterns in retailer_patterns.items():
                if any(re.search(p, label_lower) for p in patterns):
                    _record(
                        key,
                        pick_best_price(prices, query=query, retailer_key=key),
                        source,
                    )

        # Proximity: retailer mention in HTML near a dollar price
        for key, patterns in retailer_patterns.items():
            for pat in patterns:
                for m in re.finditer(pat, blob, re.IGNORECASE):
                    window = blob[m.start() : m.start() + 900]
                    plain = re.sub(r"<[^>]+>", " ", window)
                    prices = extract_prices_from_visible_text(plain)
                    if prices:
                        _record(
                            key,
                            pick_best_price(prices, query=query, retailer_key=key),
                            source,
                        )

        lines = blob.splitlines()
        for i, line in enumerate(lines):
            lower = line.lower()
            window = "\n".join(lines[max(0, i - 1) : i + 4])
            prices = extract_prices_from_visible_text(window)
            if not prices:
                continue
            for key, patterns in retailer_patterns.items():
                if any(re.search(p, lower) for p in patterns):
                    _record(
                        key,
                        pick_best_price(prices, query=query, retailer_key=key),
                        source,
                    )

    _scan_blob(page_text or "", "regex_text")
    _scan_blob(html or "", "regex_html")

    listings = list(found.values())
    listings.sort(key=lambda x: x.get("price") or float("inf"))
    return listings


def price_rich_excerpt(page_text: str, html: str, max_len: int = 24_000) -> str:
    """Keep only lines/chunks that likely contain merchant + price signals."""
    plain = re.sub(r"<[^>]+>", " ", f"{page_text or ''}\n{html or ''}")
    plain = re.sub(r"\s+", " ", plain)
    retailers = ("walmart", "amazon", "ebay", "best buy", "target", "osmo", "pocket", "dji")
    chunks: list[str] = []
    for m in re.finditer(r".{0,120}\$\d[\d,]*(?:\.\d{2})?.{0,120}", plain, re.IGNORECASE):
        chunk = m.group(0)
        if any(r in chunk.lower() for r in retailers):
            chunks.append(chunk.strip())
    if not chunks:
        for token in retailers:
            for m in re.finditer(rf".{{0,80}}{token}.{{0,200}}", plain, re.IGNORECASE):
                chunks.append(m.group(0).strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for c in chunks:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    excerpt = "\n".join(deduped)
    return excerpt[:max_len] if excerpt else (page_text or "")[:max_len]


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
