"""ASP scrape request options (mirrors managed scrape API semantics)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScrapeOptions:
    url: str
    asp: bool = True
    render_js: bool = True
    retailer_key: str = ""
    proxy: str | None = None
    retry_on_block: bool = True
