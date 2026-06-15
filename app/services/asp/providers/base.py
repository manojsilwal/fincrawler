"""ASP provider interface — pluggable scrape backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ScrapeContext:
    url: str
    crawled_at: str
    retailer_key: str = ""
    proxy: str | None = None
    asp: bool = True
    render_js: bool = True


class AspProvider(ABC):
    """One step in the FinCrawler ASP escalation ladder."""

    name: str

    def _circuit_ok(self) -> bool:
        from app.services.asp.provider_health import is_budget_exceeded, is_circuit_open

        if is_circuit_open(self.name):
            return False
        if self.name.startswith("brightdata") or self.name == "external_scrapfly":
            if is_budget_exceeded():
                return False
        return True

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when credentials/config allow this provider."""

    def should_try(self, ctx: ScrapeContext, profile: dict) -> bool:
        return self.is_available() and self._circuit_ok()

    @abstractmethod
    async def fetch(self, ctx: ScrapeContext) -> dict:
        """Execute scrape; return normalized crawl dict."""
