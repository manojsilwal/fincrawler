# FinCrawler — ASP Platform Architecture (Phases 1–3)

## Overview

Built-in ASP replaces Bright Data/Scrapfly as the default path. External providers are optional plugins auto-disabled on suspension or budget exceed.

```mermaid
flowchart TB
    Client[Zenith / POST /asp/scrape] --> Engine[AspEngine]
    Engine --> Registry[providers/registry]
    Registry --> Grid[browser_grid]
    Registry --> Curl[http_impersonate]
    Registry --> Local[js_browser]
    Registry --> Captcha[captcha_browser]
    Registry --> Proxy[proxy_http]
    Registry --> BD[brightdata optional]
    Grid --> Redis[(Redis queue)]
    Redis --> Workers[browser_grid_worker x N]
    Proxy --> Pool[proxy_pool]
    Pool --> SP[Smartproxy]
    Pool --> IR[IPRoyal]
    Pool --> OX[Oxylabs]
    Engine --> Health[provider_health]
    Engine --> Metrics[metrics dashboard]
```

## Phase 1 — Fully internal

| Component | Path | Config |
|-----------|------|--------|
| Browser worker pool | `workers/browser_grid_worker.py` | `ENABLE_BROWSER_GRID=true` |
| GCP 2–4 VM deploy | `scripts/gcp/deploy_browser_grid.sh` | `gcloud` MIG |
| **Internal egress fleet** | `asp/egress_registry.py`, `proxy_backends/internal_egress.py` | `INTERNAL_EGRESS_ENDPOINTS`, `PROXY_BACKEND=internal` |
| Squid forward proxies | `docker/squid/`, `scripts/gcp/deploy_internal_egress.sh` | Multi-VM GCP egress IPs |
| Browser proxy rotation | `crawler/browser_fetcher.py` | `BROWSER_PROXY_ENABLED=true` |
| Proxy backends (optional paid) | `asp/proxy_backends/` | `SMARTPROXY_*`, `IPROYAL_*`, `OXYLABS_*` |
| CAPTCHA tier | `asp/captcha/`, `providers/captcha_browser.py` | `CAPSOLVER_API_KEY`, `TWOCAPTCHA_API_KEY` |
| Single API | `POST /asp/scrape` | unchanged |

**Internal egress (Bright Data alternative):**

```mermaid
flowchart LR
    API[FinCrawler API] --> Pool[proxy_pool]
    Pool --> Squid1[Squid VM us-central1]
    Pool --> Squid2[Squid VM us-east1]
    Pool --> Workers[browser_grid workers]
    Workers --> Redis[(egress_registry)]
    Pool --> Playwright[Playwright via proxy]
```

- `INTERNAL_EGRESS_ENDPOINTS` — comma-separated Squid URLs on your GCP VMs
- `INTERNAL_EGRESS_USE_WORKER_SLOTS` — route jobs to worker egress (`direct://worker-id`)
- `PROXY_POOL_REDIS` — shared sticky sessions + health across API and workers
- `BROWSER_PROXY_MAX_RETRIES` — rotate egress on captcha/access_denied

Deploy multi-region Squid fleet:

```bash
chmod +x scripts/gcp/deploy_internal_egress.sh
./scripts/gcp/deploy_internal_egress.sh dreamrise-gcp 3
# Copy printed INTERNAL_EGRESS_ENDPOINTS into .env.gcp
```

**Provider order (default):**
`http_impersonate → browser_grid → captcha_browser → proxy_http → brightdata_* → scrapfly`

(`js_browser` is omitted when grid is enabled — grid workers already run the same stealth fetch; avoids duplicate 30–40s passes per retailer.)

## Phase 2 — Hybrid

| Feature | Module |
|---------|--------|
| Auto-disable suspended | `provider_health.py` — detects `customer_suspended`, billing errors |
| Daily budget cap | `ASP_DAILY_BUDGET_USD` — disables paid providers when exceeded |
| Circuit breaker | `PROVIDER_MAX_FAILURES`, `PROVIDER_CIRCUIT_COOLDOWN_SECONDS` |
| Enable/disable Bright Data | `ENABLE_BRIGHTDATA_PROVIDER=true/false` |

## Phase 3 — Production ASP

| Feature | Module |
|---------|--------|
| Redis job queue | `browser_grid/queue.py` |
| Proxy health scoring | `proxy_pool.py` — success/failure rates |
| Sticky sessions per retailer | `PROXY_STICKY_SESSIONS`, `PROXY_STICKY_TTL_SECONDS` |
| Fingerprint rotation | `crawler/fingerprints.py` — UA/viewport/platform per retailer |
| Metrics dashboard | `GET /asp/metrics` — block rate, latency, $/success |

## Quick start (internal only)

```bash
# .env
ENABLE_BRIGHTDATA_PROVIDER=false
ENABLE_BROWSER_GRID=true
ENABLE_INTERNAL_EGRESS=true
PROXY_BACKEND=internal
BROWSER_PROXY_ENABLED=true
PROXY_POOL_REDIS=true
ASP_PROVIDER_ORDER=http_impersonate,browser_grid,captcha_browser,proxy_http
BROWSER_GRID_WORKER_CONCURRENCY=2
BROWSER_GRID_TIMEOUT_SECONDS=75

# Local with Squid egress sidecar
docker compose -f docker-compose.gcp.yml up -d egress-proxy fincrawler browser-grid-worker

# GCP multi-region Squid egress (3 VMs, unique external IPs)
chmod +x scripts/gcp/deploy_internal_egress.sh
./scripts/gcp/deploy_internal_egress.sh dreamrise-gcp 3

# GCP browser worker pool (3 VMs)
chmod +x scripts/gcp/deploy_browser_grid.sh
./scripts/gcp/deploy_browser_grid.sh 3 us-central1-a
```

## Proxy backend examples

```bash
# Internal self-hosted egress (default — no Bright Data / Smartproxy)
ENABLE_INTERNAL_EGRESS=true
PROXY_BACKEND=internal
INTERNAL_EGRESS_ENDPOINTS=http://user:pass@34.x.x.x:3128,http://user:pass@35.y.y.y:3128
BROWSER_PROXY_ENABLED=true
PROXY_POOL_REDIS=true

# Smartproxy residential (optional paid)
SMARTPROXY_USER=your_user
SMARTPROXY_PASSWORD=your_pass
PROXY_BACKEND=smartproxy
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/asp/scrape` | Single internal scrape API |
| GET | `/asp/health` | Provider status, queue depth, proxy pool |
| GET | `/asp/metrics` | Block rates, latency, estimated costs |
