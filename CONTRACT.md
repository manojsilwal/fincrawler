# FinCrawler Hybrid Compliant API Contract

Shared contract between **FinCrawler** (`app/`) and **Zenith Rewards** worker client.

## Tier model (hybrid)

| Tier | `tier_name` | Tool | When |
|------|-------------|------|------|
| 1 | `compliant` | Honest httpx User-Agent | First attempt for allowed URLs |
| 2 | `tls_impersonate` | Internal ASP `curl_cffi` | Fast path for light retailers |
| 3 | `stealth_browser` | Internal ASP stealth Playwright | Session warm, JS render, challenge handling |
| 4 | `bank_grade` | Internal ASP proxy fallback (`MANAGED_PROXY_URL`) | After browser block; optional external Scrapfly if `ENABLE_EXTERNAL_SCRAPFLY=true` |

Escalation triggers: CAPTCHA, `403`, `429`, login wall, `/blocked` URL, thin challenge pages.

Hard stop only when Tier 4 still blocked → source `blocked_or_rate_limited`.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/asp/scrape` | Internal ASP managed scrape |
| GET | `/asp/health` | ASP engine liveness |
| POST | `/sources` | Source registry CRUD |
| POST | `/crawl-jobs/url` | Compliant hybrid fetch single URL |
| GET | `/crawl-jobs/events` | Compliance event log |
| POST | `/shop/search` | Live multi-retailer search (Zenith primary) |
| POST | `/shop/google` | **410 Gone** — removed |
| POST | `/crawl`, `/scrape` | Zenith compat alias |
| GET | `/products/search` | DB product search |
| GET | `/rankings/search` | Ranked offers from DB |

## Response envelope

```json
{
  "status": "ok",
  "tier_used": 4,
  "tier_name": "bank_grade",
  "escalated_from": "captcha_detected",
  "detection_hits": ["captcha_detected"],
  "block_reason": null,
  "http_status": 200
}
```

## Environment

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL |
| `REDIS_URL` | Queue/cache |
| `API_KEY` | Client auth |
| `SCRAPFLY_API_KEY` | Tier 4 (required for Walmart/Amazon/eBay search) |
| `MANAGED_PROXY_URL` | Tier 4 fallback |
| `CRAWLER_USER_AGENT` | Honest bot identity for Tier 1 |
| `LLM_*` | Product extraction |

## Sources

Retailer search sources live in PostgreSQL `sources` table (`scripts/seed_sources.py`).
`source_type`: `managed_retailer_search` with `robots_policy: advisory`.
