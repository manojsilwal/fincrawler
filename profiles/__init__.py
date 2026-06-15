"""Load per-retailer crawl profiles from retailers.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROFILES_PATH = Path(__file__).parent / "retailers.json"


def load_profiles() -> dict[str, dict[str, Any]]:
    with _PROFILES_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return {k: dict(v) for k, v in data.items()}


def get_profile(retailer_key: str) -> dict[str, Any] | None:
    p = load_profiles().get(retailer_key)
    if p is None:
        return None
    return {**p, "retailer_key": retailer_key}


def retailers_dict() -> dict[str, dict]:
    """Shape compatible with legacy RETAILERS in shop_crawler."""
    out: dict[str, dict] = {}
    for key, p in load_profiles().items():
        if key == "google_shopping":
            continue
        out[key] = {
            "name": p["name"],
            "search_url": p["search_url"],
            "wait_selector": p.get("wait_selector", ""),
            "consent_selectors": p.get("consent_selectors", []),
            "homepage_url": p.get("homepage_url"),
            "default_tier": p.get("default_tier", 2),
            "warm_session": p.get("warm_session", True),
        }
    return out
