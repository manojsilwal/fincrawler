"""Pytest configuration — SQLite in-memory for unit tests."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_shopping_intel.db")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("ENABLE_BROWSER_TIER4", "false")
