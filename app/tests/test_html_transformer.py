"""Unit tests for HTML transformer (markdown / links)."""

from app.services.crawler.html_transformer import (
    extract_links,
    html_to_markdown,
    transform_page,
)


SAMPLE = """
<html><head><title>T</title></head>
<body>
<header>nav</header>
<main>
  <h1>Widget Pro</h1>
  <p>Buy the <a href="/p/123">Widget Pro</a> today.</p>
  <p>Price $19.99</p>
</main>
<footer>c</footer>
</body></html>
"""


def test_html_to_markdown_main_content():
    md = html_to_markdown(SAMPLE, only_main_content=True)
    assert "Widget Pro" in md
    assert "19.99" in md


def test_extract_links_absolute():
    links = extract_links(SAMPLE, "https://shop.example.com/search")
    assert any("/p/123" in u for u in links)
    assert all(u.startswith("http") for u in links)


def test_transform_page_formats():
    out = transform_page(
        SAMPLE,
        base_url="https://shop.example.com/",
        formats=["markdown", "links", "text"],
    )
    assert "markdown" in out
    assert "links" in out
    assert "text" in out
    assert out["content"] == out["markdown"]
