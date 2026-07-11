"""Tests for JS probe and product URL scoring."""

from app.services.crawler.js_probe import needs_js_rendering
from app.services.crawler.product_frontier import score_html_product_signal, score_product_url


def test_needs_js_spa_shell():
    html = '<div id="root"></div>' + ("<script></script>" * 10)
    needs, reason = needs_js_rendering(html, text=" ")
    assert needs is True
    assert reason


def test_static_page_no_js():
    html = "<html><body>" + ("<p>paragraph of content about products and prices </p>" * 20) + "</body></html>"
    text = "paragraph of content about products and prices " * 20
    needs, reason = needs_js_rendering(html, text)
    assert needs is False
    assert reason == ""


def test_score_product_url_pdp():
    assert score_product_url("https://www.amazon.com/dp/B0TEST123") > 0.5
    assert score_product_url("https://www.amazon.com/cart") < 0


def test_score_html_product_signal():
    html = '<script type="application/ld+json">{"@type":"Product","name":"X"}</script>'
    assert score_html_product_signal(html) >= 0.9
    assert score_html_product_signal("<html></html>") == 0.0
