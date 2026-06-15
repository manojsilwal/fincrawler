"""Application configuration from environment."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@lru_cache
def get_settings() -> "Settings":
    return Settings()


class Settings:
    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development")
        self.app_name = os.getenv("APP_NAME", "shopping-intel-crawler")
        self.api_key = os.getenv("API_KEY", "")

        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@localhost:5432/shopping_intel",
        )
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        self.crawler_user_agent = os.getenv(
            "CRAWLER_USER_AGENT",
            "ShoppingIntelBot/1.0 (+https://example.com/crawler-info; contact: crawler@example.com)",
        )
        self.crawler_contact_email = os.getenv("CRAWLER_CONTACT_EMAIL", "crawler@example.com")
        self.default_crawl_delay_seconds = int(os.getenv("DEFAULT_CRAWL_DELAY_SECONDS", "10"))
        self.max_requests_per_domain_per_minute = int(
            os.getenv("MAX_REQUESTS_PER_DOMAIN_PER_MINUTE", "6")
        )
        self.fetch_timeout_seconds = float(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))

        self.enable_allowed_web_crawling = (
            os.getenv("ENABLE_ALLOWED_WEB_CRAWLING", "true").lower() == "true"
        )
        self.enable_api_connectors = os.getenv("ENABLE_API_CONNECTORS", "true").lower() == "true"
        self.scrapfly_api_key = os.getenv("SCRAPFLY_API_KEY", "")
        self.enable_external_scrapfly = (
            os.getenv("ENABLE_EXTERNAL_SCRAPFLY", "false").lower() == "true"
        )
        self.brightdata_api_key = os.getenv("BRIGHTDATA_API_KEY", "")
        self.brightdata_zone = os.getenv("BRIGHTDATA_ZONE", "")
        self.brightdata_country = os.getenv("BRIGHTDATA_COUNTRY", "us")
        self.brightdata_customer_id = os.getenv("BRIGHTDATA_CUSTOMER_ID", "")
        self.brightdata_zone_password = os.getenv("BRIGHTDATA_ZONE_PASSWORD", "")
        self.brightdata_scraping_browser_wss = os.getenv("BRIGHTDATA_SCRAPING_BROWSER_WSS", "")
        self.brightdata_proxy_port = int(os.getenv("BRIGHTDATA_PROXY_PORT", "9515"))
        self.managed_proxy_url = os.getenv("MANAGED_PROXY_URL", "")
        self.managed_fetcher_mode = os.getenv("MANAGED_FETCHER_MODE", "auto")
        self.enable_browser_tier4 = os.getenv("ENABLE_BROWSER_TIER4", "true").lower() == "true"
        self.browser_pool_size = int(os.getenv("BROWSER_POOL_SIZE", "2"))
        self.browser_nav_timeout_ms = int(os.getenv("BROWSER_NAV_TIMEOUT_MS", "35000"))
        self.browser_headless = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
        self.challenge_wait_ms = int(os.getenv("CHALLENGE_WAIT_MS", "12000"))

        self.asp_provider_order = os.getenv(
            "ASP_PROVIDER_ORDER",
            "browser_grid,http_impersonate,js_browser,captcha_browser,proxy_http,"
            "brightdata_scraping_browser,brightdata_unlocker,external_scrapfly",
        )
        self.enable_brightdata_provider = (
            os.getenv("ENABLE_BRIGHTDATA_PROVIDER", "true").lower() == "true"
        )

        # Proxy pool — static + Smartproxy / IPRoyal / Oxylabs
        self.proxy_pool_urls = os.getenv("PROXY_POOL_URLS", "")
        self.proxy_provider = os.getenv("PROXY_PROVIDER", "auto")
        self.proxy_backend = os.getenv("PROXY_BACKEND", "auto")
        self.proxy_max_failures = int(os.getenv("PROXY_MAX_FAILURES", "3"))
        self.proxy_sticky_sessions = os.getenv("PROXY_STICKY_SESSIONS", "true").lower() == "true"
        self.proxy_sticky_ttl_seconds = int(os.getenv("PROXY_STICKY_TTL_SECONDS", "1800"))
        self.proxy_pool_redis = os.getenv("PROXY_POOL_REDIS", "true").lower() == "true"
        self.enable_internal_egress = (
            os.getenv("ENABLE_INTERNAL_EGRESS", "true").lower() == "true"
        )
        self.internal_egress_endpoints = os.getenv("INTERNAL_EGRESS_ENDPOINTS", "")
        self.internal_egress_use_worker_slots = (
            os.getenv("INTERNAL_EGRESS_USE_WORKER_SLOTS", "true").lower() == "true"
        )
        self.browser_proxy_enabled = (
            os.getenv("BROWSER_PROXY_ENABLED", "true").lower() == "true"
        )
        self.browser_proxy_max_retries = int(os.getenv("BROWSER_PROXY_MAX_RETRIES", "3"))

        self.smartproxy_user = os.getenv("SMARTPROXY_USER", "")
        self.smartproxy_password = os.getenv("SMARTPROXY_PASSWORD", "")
        self.smartproxy_host = os.getenv("SMARTPROXY_HOST", "gate.smartproxy.com")
        self.smartproxy_port = int(os.getenv("SMARTPROXY_PORT", "10000"))

        self.iproyal_user = os.getenv("IPROYAL_USER", "")
        self.iproyal_password = os.getenv("IPROYAL_PASSWORD", "")
        self.iproyal_host = os.getenv("IPROYAL_HOST", "geo.iproyal.com")
        self.iproyal_port = int(os.getenv("IPROYAL_PORT", "12321"))

        self.oxylabs_user = os.getenv("OXYLABS_USER", "")
        self.oxylabs_password = os.getenv("OXYLABS_PASSWORD", "")
        self.oxylabs_host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
        self.oxylabs_port = int(os.getenv("OXYLABS_PORT", "7777"))

        # CAPTCHA solvers
        self.capsolver_api_key = os.getenv("CAPSOLVER_API_KEY", "")
        self.twocaptcha_api_key = os.getenv("TWOCAPTCHA_API_KEY", "")
        self.captcha_provider = os.getenv("CAPTCHA_PROVIDER", "auto")

        # In-house antibot solver (PerimeterX press-and-hold, DataDome slider)
        self.enable_antibot_solver = os.getenv("ENABLE_ANTIBOT_SOLVER", "true").lower() == "true"
        self.antibot_px_hold_ms_min = int(os.getenv("ANTIBOT_PX_HOLD_MS_MIN", "2800"))
        self.antibot_px_hold_ms_max = int(os.getenv("ANTIBOT_PX_HOLD_MS_MAX", "5200"))
        self.antibot_max_attempts = int(os.getenv("ANTIBOT_MAX_ATTEMPTS", "3"))
        self.antibot_cookie_ttl_seconds = int(os.getenv("ANTIBOT_COOKIE_TTL_SECONDS", "3600"))

        # Provider health / budget (Phase 2 hybrid)
        self.provider_max_failures = int(os.getenv("PROVIDER_MAX_FAILURES", "5"))
        self.provider_circuit_cooldown_seconds = int(
            os.getenv("PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "3600")
        )
        self.asp_daily_budget_usd = float(os.getenv("ASP_DAILY_BUDGET_USD", "0"))

        self.enable_browser_grid = os.getenv("ENABLE_BROWSER_GRID", "false").lower() == "true"
        self.browser_grid_queue_key = os.getenv(
            "BROWSER_GRID_QUEUE_KEY", "fincrawler:browser_grid:jobs"
        )
        self.browser_grid_timeout_seconds = float(
            os.getenv("BROWSER_GRID_TIMEOUT_SECONDS", "120")
        )
        self.browser_grid_poll_interval_ms = int(
            os.getenv("BROWSER_GRID_POLL_INTERVAL_MS", "500")
        )

        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.snapshot_dir = os.getenv("SNAPSHOT_DIR", "data/snapshots")
