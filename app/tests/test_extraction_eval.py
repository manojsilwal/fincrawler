"""Extraction eval harness — golden HTML fixtures (F1-style field checks)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.crawler.html_product_extractor import extract_product_fields
from app.services.crawler.selector_healer import propose_css_selectors

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> tuple[str, dict]:
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return html, expected


def test_golden_product_extraction_f1_fields():
    html, expected = _load_fixture("product_basic")
    got = extract_product_fields(html, "")
    assert got.get("_error") is None
    # Exact matches for core fields
    assert got["title"] == expected["title"]
    assert got["price"] == expected["price"]
    assert got.get("brand") == expected.get("brand")


def test_selector_healer_proposes_title():
    html, _ = _load_fixture("product_basic")
    sels = propose_css_selectors(html, "title")
    assert isinstance(sels, list)


def test_field_level_recall():
    """Simple recall over required fields — eval harness SLO."""
    html, expected = _load_fixture("product_basic")
    got = extract_product_fields(html, "")
    required = ["title", "price"]
    hits = sum(1 for k in required if got.get(k) == expected.get(k))
    recall = hits / len(required)
    assert recall >= 1.0
