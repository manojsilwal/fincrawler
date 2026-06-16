"""Finance compat helpers — offline unit tests."""

import unittest

from app.api.finance_compat import _parse_news_articles, _parse_yahoo_regular_price


class TestFinanceCompat(unittest.TestCase):
    def test_parse_yahoo_regular_price_embedded_json(self):
        html = '{"regularMarketPrice":{"raw":198.42},"shortName":"Apple Inc."}'
        self.assertEqual(_parse_yahoo_regular_price(html), 198.42)

    def test_parse_yahoo_regular_price_flat(self):
        html = '"regularMarketPrice":201.5,'
        self.assertEqual(_parse_yahoo_regular_price(html), 201.5)

    def test_parse_news_articles_from_h3(self):
        html = '<a href="/news/1"><h3>Apple beats estimates on services growth</h3></a>'
        articles = _parse_news_articles(html, limit=3)
        self.assertEqual(len(articles), 1)
        self.assertIn("Apple", articles[0]["title"])


if __name__ == "__main__":
    unittest.main()
