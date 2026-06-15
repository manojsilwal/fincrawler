"""Tests for internal ASP engine."""

from app.services.asp import AspEngine, ScrapeOptions, asp_engine
from app.services.asp.detector import has_product_markers, is_usable_scrape


def test_asp_engine_singleton():
    assert asp_engine.service_name == "fincrawler-asp"


def test_scrape_options_defaults():
    opts = ScrapeOptions(url="https://example.com")
    assert opts.asp is True
    assert opts.render_js is True
    assert opts.retry_on_block is True


def test_product_markers():
    profile = {"product_markers": ["sku-title"]}
    assert has_product_markers("<div class='sku-title'>X</div>", profile)
    assert not has_product_markers("<div>empty</div>", profile)


def test_is_usable_scrape():
    profile = {"product_markers": ["s-search-result"]}
    ok = {
        "status": "ok",
        "html": "<div data-component-type='s-search-result'>item</div>",
    }
    assert is_usable_scrape(ok, "amazon") is True
    bad = {"status": "blocked", "html": ""}
    assert is_usable_scrape(bad, "amazon") is False


def test_asp_engine_class():
    engine = AspEngine()
    assert engine.service_name == "fincrawler-asp"
