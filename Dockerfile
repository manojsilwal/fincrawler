# syntax=docker/dockerfile:1
# ── FinCrawler Dockerfile ─────────────────────────────────────────────────
# Multi-stage build:
#   stage 1 (deps)    — install Python packages into a clean venv
#   stage 2 (runtime) — copy the venv + app code; install Playwright browsers
#
# Fix: Playwright browsers are installed to /ms-playwright (world-readable)
#      so they work whether running as root or as appuser.
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Chromium system runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxi6 \
    libxtst6 \
    fonts-liberation \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ── Key fix: install browsers to a world-readable location ────────────────
# Without this, browsers go to /root/.cache and are invisible to appuser.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium \
    && chmod -R o+rx /ms-playwright

WORKDIR /app
COPY . .

EXPOSE 10000

RUN useradd --create-home appuser
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000", "--workers", "1"]
