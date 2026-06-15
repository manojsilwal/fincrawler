"""In-memory retailer session warming cache."""

from __future__ import annotations

import time

_warmed: dict[str, float] = {}
_TTL_SECONDS = 1800


def is_warmed(retailer_key: str) -> bool:
    ts = _warmed.get(retailer_key)
    if ts is None:
        return False
    if time.time() - ts > _TTL_SECONDS:
        _warmed.pop(retailer_key, None)
        return False
    return True


def mark_warmed(retailer_key: str) -> None:
    _warmed[retailer_key] = time.time()


def clear_warmed(retailer_key: str | None = None) -> None:
    if retailer_key:
        _warmed.pop(retailer_key, None)
    else:
        _warmed.clear()
