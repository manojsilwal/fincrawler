"""Yahoo Finance service unit tests."""

import unittest

from app.services.yahoo_finance import (
    _merge_flat_with_vision,
    _strip_news_from_flat,
    _yahoo_api_succeeded,
    extract_quote_from_fetch_result,
    flatten_yahoo_modules,
    parse_fin_streamers_from_html,
    parse_news_articles_from_html,
    parse_price_for_symbol,
    parse_yahoo_regular_price,
    ticker_from_yahoo_url,
)


class TestYahooFinance(unittest.TestCase):
    def test_ticker_from_url(self):
        self.assertEqual(
            ticker_from_yahoo_url("https://finance.yahoo.com/quote/HPE/"),
            "HPE",
        )
        self.assertEqual(
            ticker_from_yahoo_url("https://finance.yahoo.com/quote/aapl/key-statistics"),
            "AAPL",
        )
        self.assertIsNone(ticker_from_yahoo_url("https://example.com/"))

    def test_parse_price_for_symbol_avoids_wrong_ticker(self):
        html = (
            '{"symbol":"BTC-USD","regularMarketPrice":{"raw":62361.87},"shortName":"Bitcoin USD"}'
            '{"symbol":"HPE","regularMarketPrice":{"raw":47.41},"shortName":"Hewlett Packard Enterprise"}'
        )
        scoped = parse_price_for_symbol(html, "HPE")
        self.assertEqual(scoped.get("regularMarketPrice"), 47.41)
        self.assertEqual(scoped.get("shortName"), "Hewlett Packard Enterprise")

    def test_parse_yahoo_regular_price_with_symbol(self):
        html = '{"symbol":"HPE","regularMarketPrice":{"raw":47.41}}'
        self.assertEqual(parse_yahoo_regular_price(html, "HPE"), 47.41)

    def test_flatten_yahoo_modules(self):
        modules = {
            "price": {
                "regularMarketPrice": {"raw": 47.41, "fmt": "47.41"},
                "shortName": "HPE",
            },
            "summaryDetail": {"marketCap": {"raw": 62000000000}},
        }
        flat = flatten_yahoo_modules(modules)
        self.assertEqual(flat["price.regularMarketPrice"], 47.41)
        self.assertEqual(flat["price.shortName"], "HPE")
        self.assertEqual(flat["summaryDetail.marketCap"], 62000000000)

    def test_parse_fin_streamers_from_html(self):
        html = (
            '<fin-streamer data-symbol="HPE" data-field="regularMarketPrice" value="47.41">'
            '<fin-streamer data-symbol="HPE" data-field="marketCap" value="62000000000">'
        )
        out = parse_fin_streamers_from_html(html, "HPE")
        self.assertEqual(out["regularMarketPrice"], 47.41)
        self.assertEqual(out["marketCap"], 62000000000.0)

    def test_extract_quote_from_fetch_result(self):
        result = {
            "html": '<fin-streamer data-symbol="HPE" data-field="regularMarketPrice" value="47.41">',
            "page_text": "Hewlett Packard Enterprise",
        }
        flat = extract_quote_from_fetch_result(result, "HPE")
        self.assertEqual(flat["asp.regularMarketPrice"], 47.41)

    def test_yahoo_api_succeeded(self):
        self.assertFalse(_yahoo_api_succeeded({}))
        self.assertTrue(_yahoo_api_succeeded({"price": {"regularMarketPrice": {"raw": 1.0}}}))

    def test_merge_flat_with_vision_prefers_vision_price(self):
        flat = {"asp.regularMarketPrice": 62361.87, "asp.ticker": "BTC-USD"}
        vision = {"vision.regularMarketPrice": 47.41, "vision.ticker": "HPE"}
        merged = _merge_flat_with_vision(flat, vision)
        self.assertEqual(merged["vision.regularMarketPrice"], 47.41)
        self.assertNotIn("asp.regularMarketPrice", merged)

    def test_strip_news_from_flat(self):
        flat = {
            "vision.regularMarketPrice": 47.41,
            "vision.news": [{"headline": "x"}],
            "news": [{"title": "y"}],
        }
        stripped = _strip_news_from_flat(flat)
        self.assertIn("vision.regularMarketPrice", stripped)
        self.assertNotIn("vision.news", stripped)
        self.assertNotIn("news", stripped)

    def test_parse_news_articles_from_html(self):
        html = '<a href="/news/1"><h3>Apple beats estimates on services growth</h3></a>'
        articles = parse_news_articles_from_html(html, limit=3)
        self.assertEqual(len(articles), 1)
        self.assertIn("Apple", articles[0]["title"])


if __name__ == "__main__":
    unittest.main()
