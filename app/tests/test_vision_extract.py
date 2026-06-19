"""Tests for vision merge and scroll screenshot helpers."""

import unittest

from llm import _merge_vision_dicts


class TestVisionMerge(unittest.TestCase):
    def test_merge_prefers_first_non_null(self):
        a = {"regularMarketPrice": 47.41, "ticker": "HPE"}
        b = {"regularMarketPrice": 99.0, "marketCap": 62000000000}
        merged = _merge_vision_dicts([a, b])
        self.assertEqual(merged["regularMarketPrice"], 47.41)
        self.assertEqual(merged["marketCap"], 62000000000)

    def test_merge_dedupes_news_lists(self):
        a = {"news": [{"headline": "A", "source": "Reuters"}]}
        b = {"news": [{"headline": "A", "source": "Reuters"}, {"headline": "B", "source": "Bloomberg"}]}
        merged = _merge_vision_dicts([a, b])
        self.assertEqual(len(merged["news"]), 2)

    def test_merge_nested_sections(self):
        a = {"quote_header": {"ticker": "HPE", "regularMarketPrice": 47.41}}
        b = {"quote_header": {"company_name": "HPE Inc"}}
        merged = _merge_vision_dicts([a, b])
        self.assertEqual(merged["quote_header"]["ticker"], "HPE")
        self.assertEqual(merged["quote_header"]["company_name"], "HPE Inc")


if __name__ == "__main__":
    unittest.main()
