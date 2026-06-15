"""Product matcher tests."""

from app.database import Base, SessionLocal, engine, init_db
from app.models import Product
from app.services.matching.product_matcher import ProductMatcher


def _db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    return SessionLocal()


def test_gtin_exact_match():
    db = _db()
    p = Product(canonical_title="AirPods", gtin="1234567890123", normalized_key="k1")
    db.add(p)
    db.commit()
    m = ProductMatcher().find_or_create_product(
        db, {"canonical_title": "AirPods Pro", "gtin": "1234567890123", "normalized_key": "k2"}
    )
    assert m["match_type"] == "gtin_exact"
    assert m["product_id"] == p.id


def test_normalized_key_match():
    db = _db()
    p = Product(canonical_title="Osmo Pocket 3", normalized_key="dji|osmo pocket 3|")
    db.add(p)
    db.commit()
    m = ProductMatcher().find_or_create_product(
        db, {"canonical_title": "Osmo Pocket 3", "normalized_key": "dji|osmo pocket 3|"}
    )
    assert m["match_type"] == "normalized_key"


def test_new_product_created():
    db = _db()
    m = ProductMatcher().find_or_create_product(
        db, {"canonical_title": "Unique Product XYZ", "normalized_key": "unique|product|xyz"}
    )
    assert m["match_type"] == "new_product"
    assert db.query(Product).count() == 1
