"""AutoCrawler-inspired selector self-heal for retailer HTML extractors."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).resolve().parents[3] / "profiles" / "retailers.json"


def _load_profiles() -> dict[str, Any]:
    if not _PROFILES_PATH.exists():
        return {}
    return json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))


def _save_profiles(data: dict[str, Any]) -> None:
    _PROFILES_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def propose_css_selectors(html: str, field: str = "title") -> list[str]:
    """Heuristic DOM prune: suggest CSS selectors from common product patterns."""
    suggestions: list[str] = []
    patterns = {
        "title": [
            r'<(?:h1)[^>]*class=["\']([^"\']+)["\']',
            r'<(?:span|div)[^>]*(?:data-testid|data-test|itemprop)=["\'](?:title|product-title|name)["\']',
            r'itemprop=["\']name["\']',
        ],
        "price": [
            r'itemprop=["\']price["\']',
            r'class=["\']([^"\']*price[^"\']*)["\']',
            r'data-testid=["\']([^"\']*price[^"\']*)["\']',
        ],
    }
    for pat in patterns.get(field, patterns["title"]):
        for m in re.finditer(pat, html or "", re.I):
            if m.lastindex and m.group(1):
                cls = m.group(1).strip().split()[0]
                if cls and len(cls) < 80:
                    suggestions.append(f".{cls}" if not cls.startswith((".", "#", "[")) else cls)
            else:
                # attribute selector from full match context
                attr_m = re.search(
                    r'(?:data-testid|data-test|itemprop)=["\']([^"\']+)["\']',
                    m.group(0),
                    re.I,
                )
                if attr_m:
                    suggestions.append(f'[itemprop="{attr_m.group(1)}"]' if "itemprop" in m.group(0).lower() else f'[data-testid="{attr_m.group(1)}"]')
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:8]


def heal_retailer_selectors(
    retailer_key: str,
    html: str,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """When extraction fails, derive selectors and optionally persist into profiles."""
    title_sels = propose_css_selectors(html, "title")
    price_sels = propose_css_selectors(html, "price")
    healed = {
        "title_selectors": title_sels,
        "price_selectors": price_sels,
    }
    if not persist or not retailer_key:
        return healed

    try:
        profiles = _load_profiles()
        profile = profiles.get(retailer_key) or {"name": retailer_key}
        existing_title = list(profile.get("title_selectors") or [])
        existing_price = list(profile.get("price_selectors") or [])
        for s in title_sels:
            if s not in existing_title:
                existing_title.append(s)
        for s in price_sels:
            if s not in existing_price:
                existing_price.append(s)
        profile["title_selectors"] = existing_title[:12]
        profile["price_selectors"] = existing_price[:12]
        profile["selector_healed_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
        profiles[retailer_key] = profile
        _save_profiles(profiles)
        logger.info(
            "Healed selectors for %s (title=%s price=%s)",
            retailer_key,
            len(title_sels),
            len(price_sels),
        )
    except Exception:
        logger.exception("Failed to persist healed selectors for %s", retailer_key)

    return healed


def extract_with_profile_selectors(html: str, retailer_key: str) -> dict[str, Any]:
    """Apply stored CSS-ish patterns (class/attr substring) after JSON-LD fails."""
    profiles = _load_profiles()
    profile = profiles.get(retailer_key) or {}
    out: dict[str, Any] = {}

    for sel in profile.get("title_selectors") or []:
        title = _text_near_selector(html, sel)
        if title:
            out["title"] = title[:500]
            break
    for sel in profile.get("price_selectors") or []:
        price_txt = _text_near_selector(html, sel)
        if price_txt:
            m = re.search(r"\$?\s*(\d[\d,]*\.?\d*)", price_txt)
            if m:
                try:
                    out["price"] = float(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass
    return out


def _text_near_selector(html: str, selector: str) -> str | None:
    """Very small CSS subset: .class, #id, [attr=value]."""
    if selector.startswith("."):
        cls = re.escape(selector[1:])
        m = re.search(
            rf'class=["\'][^"\']*{cls}[^"\']*["\'][^>]*>([^<]{1,200})',
            html,
            re.I,
        )
        return m.group(1).strip() if m else None
    if selector.startswith("#"):
        iid = re.escape(selector[1:])
        m = re.search(rf'id=["\']{iid}["\'][^>]*>([^<]{1,200})', html, re.I)
        return m.group(1).strip() if m else None
    m = re.search(r'\[([^=\]]+)=["\']([^"\']+)["\']\]', selector)
    if m:
        attr, val = m.group(1), re.escape(m.group(2))
        rm = re.search(
            rf'{re.escape(attr)}=["\']{val}["\'][^>]*>([^<]{1,200})',
            html,
            re.I,
        )
        return rm.group(1).strip() if rm else None
    return None
