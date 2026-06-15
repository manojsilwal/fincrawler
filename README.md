# Hybrid Compliant Shopping Intelligence

FinCrawler includes a **built-in ASP (Anti-Scraping Protection) engine** — an in-process replacement for external Scrapfly. No third-party scrape API is required by default.

## ASP engine (`app/services/asp/`)

Internal managed scrape service with escalation:

1. **http_impersonate** — `curl_cffi` Chrome TLS fingerprint
2. **js_browser** — stealth Playwright: fingerprint masking, session warming, challenge wait/reload, product-list validation
3. **proxy_http** — `MANAGED_PROXY_URL` when configured

Optional legacy: set `ENABLE_EXTERNAL_SCRAPFLY=true` + `SCRAPFLY_API_KEY` to fall back to scrapfly.io (off by default).

## Shopping stack

- **Tier 1** — honest httpx (compliant paths)
- **ASP tiers 2–4** — internal engine for `managed_retailer_search`
- **PostgreSQL** — sources, products, offers, price history
- **Live** `POST /shop/search` for Amazon, Walmart, eBay, Best Buy, Target

## Quick start

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:10000/health
curl http://localhost:10000/asp/health
python scripts/seed_sources.py
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Service health |
| `GET /asp/health` | Internal ASP engine health |
| `POST /asp/scrape` | Direct ASP scrape (`asp`, `render_js`, `retailer_key`) |
| `POST /shop/search` | Live multi-retailer compare |
| `GET /rankings/search?q=` | Ranked offers from DB |
| `POST /crawl` | Zenith-compatible hybrid fetch |

## Tests

```bash
DATABASE_URL=sqlite:///./test.db pytest app/tests -q
ENABLE_BROWSER_TIER4=true python scripts/test_retailer_full.py
```

See [CONTRACT.md](CONTRACT.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
