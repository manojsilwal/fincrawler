"""Product-signal URL scoring + scoped PDP discovery crawl (Craw4LLM-shaped)."""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Iterable
from urllib.parse import urlparse

from app.services.crawler.html_transformer import extract_links

logger = logging.getLogger(__name__)

_PDP_POSITIVE = re.compile(
    r"(/dp/|/gp/product/|/ip/|/p/|/product/|/products/|/itm/|/sku/|/pd/)",
    re.I,
)
_PDP_NEGATIVE = re.compile(
    r"(/cart|/checkout|/login|/account|/help|/customer|/seller|/store/|"
    r"/search|/s\?|/category|/browse|/wishlist|/prime)",
    re.I,
)
_PRODUCT_SIGNAL_HTML = re.compile(
    r'application/ld\+json|"@type"\s*:\s*"Product"|itemprop=["\']price["\']|'
    r'og:type["\']?\s*content=["\']product|data-asin=|sku["\']?\s*:',
    re.I,
)


def score_product_url(url: str) -> float:
    """Higher = more likely a product detail page worth scraping."""
    path = urlparse(url).path or "/"
    score = 0.0
    if _PDP_POSITIVE.search(path):
        score += 0.7
    if _PDP_NEGATIVE.search(url):
        score -= 0.8
    # Longer paths with numeric ids often are PDPs
    if re.search(r"/\d{5,}", path):
        score += 0.25
    if path.count("/") >= 3:
        score += 0.1
    return score


def score_html_product_signal(html: str) -> float:
    if not html:
        return 0.0
    return 0.9 if _PRODUCT_SIGNAL_HTML.search(html) else 0.0


def _same_scope(seed: str, candidate: str, strategy: str = "same-domain") -> bool:
    a, b = urlparse(seed), urlparse(candidate)
    if strategy == "all":
        return True
    if strategy == "same-origin":
        return a.scheme == b.scheme and a.netloc == b.netloc
    if strategy == "same-hostname":
        return a.hostname == b.hostname
    # same-domain (default): ignore www
    ah = (a.hostname or "").lower().removeprefix("www.")
    bh = (b.hostname or "").lower().removeprefix("www.")
    return bool(ah) and ah == bh


def _path_allowed(
    url: str,
    include_paths: list[str] | None,
    exclude_paths: list[str] | None,
) -> bool:
    path = urlparse(url).path or "/"
    if exclude_paths and any(re.search(p, path) for p in exclude_paths):
        return False
    if include_paths:
        return any(re.search(p, path) for p in include_paths)
    return True


async def run_scoped_pdp_crawl(
    *,
    seed_url: str,
    max_depth: int = 2,
    limit: int = 20,
    strategy: str = "same-domain",
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    retailer_key: str = "",
) -> dict:
    """BFS over same-domain links, prioritizing product-signal URLs.

    Does **not** open-web crawl — scope is always constrained by strategy/paths.
    """
    from crawler import crawl_single

    limit = max(1, min(int(limit), 50))
    max_depth = max(0, min(int(max_depth), 4))
    visited: set[str] = set()
    results: list[dict] = []
    # queue items: (url, depth, priority)
    frontier: deque[tuple[str, int]] = deque([(seed_url, 0)])
    pending_scores: dict[str, float] = {seed_url: 1.0}

    options = {"retailer_key": retailer_key} if retailer_key else {}

    while frontier and len(results) < limit:
        # Pick highest-scoring URL at the front batch
        batch = list(frontier)
        frontier.clear()
        batch.sort(key=lambda item: pending_scores.get(item[0], 0.0), reverse=True)
        url, depth = batch[0]
        for item in batch[1:]:
            frontier.append(item)

        if url in visited:
            continue
        visited.add(url)

        fetched = await crawl_single(url, options)
        html = fetched.get("html") or ""
        page_score = score_product_url(url) + score_html_product_signal(html)
        entry = {
            "url": fetched.get("url") or url,
            "status": fetched.get("status"),
            "title": fetched.get("title"),
            "product_score": round(page_score, 3),
            "tier_used": fetched.get("tier_used"),
            "http_status": fetched.get("http_status"),
        }
        if page_score >= 0.5 or depth == 0:
            entry["text_preview"] = (fetched.get("text") or fetched.get("page_text") or "")[:500]
            results.append(entry)
        elif fetched.get("status") == "ok":
            # Still count thin pages toward exploration but don't retain full body
            results.append(entry)

        if depth >= max_depth or fetched.get("status") != "ok":
            continue

        for link in extract_links(html, fetched.get("url") or url):
            if link in visited:
                continue
            if not _same_scope(seed_url, link, strategy):
                continue
            if not _path_allowed(link, include_paths, exclude_paths):
                continue
            s = score_product_url(link)
            if s < 0:
                continue
            pending_scores[link] = s
            frontier.append((link, depth + 1))

    results.sort(key=lambda r: r.get("product_score", 0), reverse=True)
    return {
        "status": "ok",
        "job_type": "pdp_crawl",
        "seed_url": seed_url,
        "pages_crawled": len(visited),
        "pages_returned": len(results),
        "pages": results[:limit],
    }
