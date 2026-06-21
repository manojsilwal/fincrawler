"""Tests for generic vision fetcher helpers."""

import unittest

from app.services.crawler.vision_fetcher import (
    flatten_vision_extracted,
    shopping_vision_has_signal,
    vision_flat_to_shopping_data,
    vision_fallback_enabled,
)


class TestVisionFetcher(unittest.TestCase):
    def test_flatten_vision_extracted(self):
        extracted = {
            "quote_header": {"regularMarketPrice": 47.41, "ticker": "HPE"},
            "news": [{"headline": "skip"}],
        }
        flat = flatten_vision_extracted(extracted, exclude_keys=("news",))
        self.assertEqual(flat["vision.quote_header.regularMarketPrice"], 47.41)
        self.assertNotIn("vision.news", flat)

    def test_shopping_vision_has_signal(self):
        self.assertTrue(shopping_vision_has_signal({"price": 99.0}))
        self.assertTrue(shopping_vision_has_signal({"products": [{"price": 1}]}))
        self.assertFalse(shopping_vision_has_signal({}))

    def test_vision_flat_to_shopping_data(self):
        flat = {"vision.product_name": "Laptop", "vision.price": 899.0}
        extracted = {"product_name": "Laptop", "price": 899.0}
        data = vision_flat_to_shopping_data(flat, extracted, query="laptop")
        self.assertEqual(data["product_name"], "Laptop")
        self.assertEqual(data["price"], 899.0)
        self.assertEqual(data["price_source"], "vision")

    def test_vision_flat_multi_product(self):
        extracted = {
            "products": [
                {"product_name": "DJI Osmo Pocket 3", "price": 419.0, "seller": "Amazon"},
                {"product_name": "DJI Osmo Pocket 3 Creator Combo", "price": 519.0, "seller": "Amazon Warehouse"},
            ]
        }
        data = vision_flat_to_shopping_data({}, extracted, query="dji osmo pocket 3")
        self.assertIn("products", data)
        self.assertGreaterEqual(len(data["products"]), 2)
        self.assertEqual(data["product_name"], "DJI Osmo Pocket 3")
        self.assertEqual(data["price"], 419.0)

    def test_vision_fallback_enabled_default(self):
        self.assertTrue(vision_fallback_enabled())


if __name__ == "__main__":
    unittest.main()
