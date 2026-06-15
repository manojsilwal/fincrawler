"""Retailer anti-bot profiles for the internal ASP engine."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PROFILES_PATH = Path(__file__).resolve().parents[2] / "data" / "retailer_profiles.json"


@lru_cache
def _load_all() -> dict:
    with _PROFILES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_retailer_profile(retailer_key: str) -> dict:
    return dict(_load_all().get(retailer_key, {}))
