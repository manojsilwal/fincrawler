"""Unit tests for shop_price_extract heuristics."""

import pytest

from shop_price_extract import (
    crawl_likely_blocked,
    extract_google_listings_from_page,
    extract_prices_from_html,
    extract_prices_from_visible_text,
    merge_shop_extraction,
    pick_best_price,
    prepare_llm_context,
    shop_result_missing_price,
)


WALMART_HTML_SNIPPET = """
<html><body>
<script>{"currentPrice":{"price":419.00,"currency":"USD"}}</script>
<div data-automation-id="product-price">$419.00</div>
<div data-automation-id="product-price">$12.99</div>
</body></html>
"""

EBAY_HTML_SNIPPET = """
<html><body>
<li class="s-item">
  <span class="s-item__price">$389.99</span>
</li>
<li class="s-item">
  <span class="s-item__price">$9.99</span>
</li>
</body></html>
"""


def test_walmart_html_price_extraction():
    prices = extract_prices_from_html(WALMART_HTML_SNIPPET, "walmart")
    assert 419.0 in prices
    assert 12.99 not in prices  # below min product threshold


def test_ebay_html_price_extraction():
    prices = extract_prices_from_html(EBAY_HTML_SNIPPET, "ebay")
    assert 389.99 in prices
    assert 9.99 not in prices


def test_visible_text_prices():
    text = "DJI Osmo Pocket 3 Now $419.00 Free shipping Case $14.99"
    prices = extract_prices_from_visible_text(text)
    assert 419.0 in prices


def test_pick_best_price_prefers_main_product():
    candidates = [419.0, 429.0, 14.99]
    best = pick_best_price(candidates, query="DJI Osmo Pocket 3", retailer_key="walmart")
    assert best == 419.0


def test_merge_shop_extraction_regex_fallback():
    llm = {"product_name": "DJI Osmo Pocket 3", "price": None, "_error": "product_not_found"}
    merged = merge_shop_extraction(
        llm,
        [419.0, 429.0],
        query="DJI Osmo Pocket 3",
        retailer_key="walmart",
    )
    assert merged["price"] == 419.0
    assert merged["price_source"] == "regex"
    assert "_error" not in merged


def test_prepare_llm_context_includes_candidates():
    context, candidates = prepare_llm_context(
        "Some page text with Now $419.00",
        WALMART_HTML_SNIPPET,
        "DJI Osmo Pocket 3",
        "walmart",
    )
    assert candidates
    assert "Pre-detected USD price candidates" in context


def test_crawl_likely_blocked_walmart_url():
    assert crawl_likely_blocked({"url": "https://www.walmart.com/blocked?url=foo", "char_count": 5000})


def test_extract_google_listings_from_page():
    text = "DJI Osmo Pocket 3\nWalmart\n$419.00\nFree delivery\nAmazon\n$429.00"
    listings = extract_google_listings_from_page(text, "", "DJI Osmo Pocket 3")
    keys = {x["retailer_key"] for x in listings}
    assert "walmart" in keys
    assert "amazon" in keys


def test_shop_result_missing_price():
    assert shop_result_missing_price({"status": "ok", "data": {"price": None}})
    assert shop_result_missing_price({"status": "ok", "data": {"_error": "product_not_found"}})
    assert not shop_result_missing_price({"status": "ok", "data": {"price": 419.0}})
    assert not shop_result_missing_price({"status": "blocked", "data": None})
