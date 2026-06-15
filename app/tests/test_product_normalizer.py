"""Product normalizer tests."""

from app.services.normalization.product_normalizer import ProductNormalizer


def test_whitespace_removed():
    out = ProductNormalizer().normalize({"title": "  DJI   Osmo   Pocket 3  "})
    assert out["canonical_title"] == "DJI Osmo Pocket 3"


def test_promo_words_removed():
    out = ProductNormalizer().normalize({"title": "DJI Osmo Pocket 3 sale deal"})
    assert "sale" not in out["canonical_title"].lower()


def test_normalized_key_generated():
    out = ProductNormalizer().normalize({"title": "AirPods Pro", "brand": "Apple", "mpn": "MTJV3"})
    assert out["normalized_key"] == "apple|airpods pro|mtjv3"


def test_missing_optional_fields_ok():
    out = ProductNormalizer().normalize({"title": "Widget"})
    assert out["canonical_title"] == "Widget"
