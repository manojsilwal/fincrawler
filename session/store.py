"""
In-memory session store for sticky cookie jars keyed by session_id.

Production deployments can swap this for Redis-backed storage.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_store: dict[str, dict[str, Any]] = {}


def get_session(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    return dict(_store.get(session_id, {}))


def save_session(session_id: str, data: dict[str, Any]) -> None:
    if not session_id:
        return
    existing = _store.get(session_id, {})
    existing.update(data)
    _store[session_id] = existing
    logger.debug("Session saved: %s keys=%s", session_id, list(existing.keys()))


def mark_warmed(session_id: str, retailer_key: str) -> None:
    save_session(session_id, {"warmed": {**(get_session(session_id).get("warmed") or {}), retailer_key: True}})


def is_warmed(session_id: str, retailer_key: str) -> bool:
    warmed = get_session(session_id).get("warmed") or {}
    return bool(warmed.get(retailer_key))
