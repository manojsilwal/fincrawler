# FinCrawler Tiered Crawl API Contract

Shared contract between **FinCrawler** (this repo) and **Zenith Rewards** worker client.

Zenith sends tier hints via `fincrawler_client.py`; this service implements the engine in:

- `tier_router.py` — escalation across tiers
- `fetchers/tier1_curl_cffi.py` — TLS impersonation (curl_cffi, httpx fallback)
- `fetchers/tier2_scrapling.py` — JS render (Scrapling, Tier 3 fallback)
- `fetchers/tier3_stealth_browser.py` — Playwright stealth (Camoufox/CloakBrowser-class)
- `fetchers/tier4_managed.py` — Scrapfly / proxy-backed fetch
- `behavior/human_sim.py` — mouse, scroll, dwell simulation
- `session/store.py` — sticky sessions by `session_id`
- `profiles/retailers.json` — per-retailer default tiers

## Tier model

| Tier | `tier_name` | Tool | When to use |
|------|-------------|------|-------------|
| 1 | `static_api` | curl_cffi (TLS impersonation) | Static HTML, public APIs |
| 2 | `js_rendered` | Scrapling StealthyFetcher | JS-rendered SPAs |
| 3 | `advanced_antibot` | Playwright stealth + human sim | Turnstile / DataDome / behavioral ML |
| 4 | `bank_grade` | Scrapfly / managed proxy + warm identity | Hard targets after Tier 3 fails |

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/scrape` | Tiered URL scrape |
| POST | `/crawl` | Alias of `/scrape` (Zenith default path) |
| POST | `/shop/search` | Multi-retailer fan-out with per-retailer escalation |
| POST | `/shop/google?query=...` | Google Shopping; optional JSON body for tier envelope |
| POST | `/extract` | Crawl + LLM extract (uses tiered crawl) |

## Request envelope (optional on crawl endpoints)

```json
{
  "tier": 3,
  "tier_name": "advanced_antibot",
  "max_tier": 4,
  "auto_escalate": true,
  "session_id": "amazon-uuid",
  "warm_session": true,
  "proxy": { "url": "http://...", "sticky": true, "geo": "us" },
  "behavior": { "mouse": true, "scroll": true, "dwell_ms": 1200, "resource_completeness": true },
  "retailer_key": "amazon",
  "fingerprint_profile": "chrome_mac_us"
}
```

## Response envelope (on every crawl result)

```json
{
  "status": "ok",
  "tier_used": 3,
  "tier_name": "advanced_antibot",
  "detection_hits": [],
  "block_reason": null,
  "session_id": "amazon-abc123",
  "http_status": 200
}
```

### Detection hit vocabulary

`tls`, `http2_frame`, `browser_fingerprint`, `automation_signals`, `ip_reputation`, `behavioral_ml`, `captcha`, `js_pow`, `request_patterns`, `cookie_session`

## Environment variables

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Auth header |
| `SCRAPFLY_API_KEY` | Tier 4 managed API |
| `MANAGED_PROXY_URL` | Tier 4 proxy fallback |
| `LLM_*` | DeepSeek extraction |

## Per-retailer profiles

See `profiles/retailers.json`. Zenith may override via request envelope.
