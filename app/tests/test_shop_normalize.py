"""Tests for multi-product shop normalization."""

import unittest

from shop_price_extract import normalize_shop_data


class TestShopNormalize(unittest.TestCase):
    def test_normalize_multi_product_payload(self) -> None:
        data = normalize_shop_data(
            {
                "products": [
                    {"product_name": "Desk Lamp Pro", "price": 49.99, "seller": "Store A"},
                    {"product_name": "Desk Lamp Basic", "price": 29.99, "seller": "Store B"},
                    {"product_name": "Desk Lamp Pro", "price": 49.99, "seller": "Store A"},
                ]
            },
            "desk lamp",
        )
        self.assertGreaterEqual(len(data["products"]), 2)
        self.assertEqual(data["price"], 29.99)
        self.assertEqual(data["product_name"], "Desk Lamp Basic")


if __name__ == "__main__":
    unittest.main()
