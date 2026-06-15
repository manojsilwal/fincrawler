"""Background crawl worker (Redis queue stub — processes seed on startup)."""

from __future__ import annotations

import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    logger.info("Crawl worker started (stub — use POST /crawl-jobs/url or /shop/search)")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
