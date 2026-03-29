# syntax=docker/dockerfile:1
# ── FinCrawler Dockerfile ─────────────────────────────────────────────────
# Multi-stage build:
#   stage 1 (deps)    — install Python packages into a clean venv
#   stage 2 (runtime) — copy the venv + app code; install Playwright browsers
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS deps

WORKDIR /app

# Install system build deps needed by some packages (e.g. msgpack)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual-env to keep the image lean
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Playwright needs these system libs for Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime deps
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

# Copy the virtualenv from the deps stage
COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Playwright Chromium browser (runs inside the venv)
RUN playwright install chromium

WORKDIR /app

# Copy application source
COPY . .

# Render exposes port 10000 by default
EXPOSE 10000

# Tighten: run as non-root
RUN useradd --create-home appuser
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000", "--workers", "1"]
