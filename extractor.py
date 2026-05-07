# extractor.py
"""
Intelligent extraction pipeline: Fetch → Chunk → Retrieve → LLM Extract.

This module closes the biggest gap between FinCrawler and ScrapeGraphAI:
instead of dumping raw page text at the caller, we run an LLM extraction
step here and return structured, validated data.

Pipeline
--------
1. crawl_single(url)        — get raw page text via Playwright
2. chunk_text(text)         — split into ≤3K-token windows with overlap
3. select_chunks(chunks, q) — keep the N most relevant chunks (keyword score)
4. extract_structured(...)  — send retrieved chunks to DeepSeek; parse JSON
5. validate(result, schema) — optional Pydantic validation
"""

import hashlib
import logging
import re
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from cache import cache
from crawler import crawl_single
from llm import extract_structured

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chunking constants
# ---------------------------------------------------------------------------
_CHARS_PER_TOKEN = 4          # rough average for English + HTML text
_CHUNK_TOKENS    = 3_000      # chars per chunk ≈ 12 000 chars
_OVERLAP_TOKENS  = 200        # overlap between chunks ≈ 800 chars
_CHUNK_SIZE      = _CHUNK_TOKENS  * _CHARS_PER_TOKEN   # 12 000
_OVERLAP_SIZE    = _OVERLAP_TOKENS * _CHARS_PER_TOKEN  #    800
_TOP_K_CHUNKS    = 4          # how many chunks to pass to the LLM
_MAX_LLM_CHARS   = _TOP_K_CHUNKS * _CHUNK_SIZE         # ≤ 48 000 chars to LLM


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str) -> list[str]:
    """
    Split ``text`` into overlapping windows of ≈ 3 K tokens each.

    Uses paragraph-aware splitting: tries to break at double-newlines first,
    then falls back to hard character splits so no chunk exceeds _CHUNK_SIZE.
    """
    if len(text) <= _CHUNK_SIZE:
        return [text]

    # Split on paragraph boundaries first
    paragraphs = re.split(r"\n{2,}", text)

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # If a single paragraph is larger than the chunk size, hard-split it
        if len(para) > _CHUNK_SIZE:
            # flush whatever we have
            if current.strip():
                chunks.append(current.strip())
                current = ""
            # hard-split the big paragraph
            for i in range(0, len(para), _CHUNK_SIZE - _OVERLAP_SIZE):
                chunks.append(para[i : i + _CHUNK_SIZE])
            continue

        # Would adding this paragraph overflow the chunk?
        if len(current) + len(para) + 2 > _CHUNK_SIZE:
            if current.strip():
                chunks.append(current.strip())
            # Keep overlap: last _OVERLAP_SIZE chars of previous chunk
            current = current[-_OVERLAP_SIZE:] + "\n\n" + para if current else para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


# ---------------------------------------------------------------------------
# Chunk relevance scoring (keyword-based, no embedding needed)
# ---------------------------------------------------------------------------

def _score_chunk(chunk: str, query: str) -> float:
    """
    Simple TF-style keyword relevance score between a chunk and the query.
    We lower-case and tokenise, then count matching terms.
    Good enough for retrieval when we already have a structured prompt.
    """
    query_terms = set(re.findall(r"\w+", query.lower()))
    chunk_lower = chunk.lower()
    chunk_words = re.findall(r"\w+", chunk_lower)
    if not chunk_words:
        return 0.0
    hits = sum(1 for w in chunk_words if w in query_terms)
    # Normalise by chunk length to avoid always picking the longest chunk
    return hits / (len(chunk_words) ** 0.5)


def select_top_chunks(chunks: list[str], query: str, k: int = _TOP_K_CHUNKS) -> list[str]:
    """Return the *k* most relevant chunks for *query*, in document order."""
    if len(chunks) <= k:
        return chunks
    scored = [(i, _score_chunk(c, query)) for i, c in enumerate(chunks)]
    top_indices = sorted(
        sorted(scored, key=lambda x: x[1], reverse=True)[:k],
        key=lambda x: x[0],   # restore document order
    )
    return [chunks[i] for i, _ in top_indices]


# ---------------------------------------------------------------------------
# Cache key for extraction results
# ---------------------------------------------------------------------------

def _extract_cache_key(url: str, prompt: str) -> str:
    """Composite cache key: hash(url + prompt) so same URL with different prompts → different cache entries."""
    raw = f"{url}||{prompt.strip().lower()}"
    return "extract:" + hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_from_page(
    url: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    force_refresh: bool = False,
    extra_context: Optional[str] = None,
) -> dict[str, Any]:
    """
    Full extraction pipeline for a single URL.

    Parameters
    ----------
    url:
        Target web page URL.
    prompt:
        Natural language extraction instruction.
        E.g. "Extract current price, P/E ratio, 52-week high and low, and volume."
    schema:
        Optional Pydantic model.  When provided, the result is validated
        and coerced to this schema.
    force_refresh:
        Bypass cache even on a hit.
    extra_context:
        Optional ticker / domain hint injected into the LLM system prompt.

    Returns
    -------
    dict with keys:
        url           str
        prompt        str
        data          dict  — extracted fields (or {_error: ...} on failure)
        cache_hit     bool
        chunks_used   int
        total_chunks  int
        status        "ok" | "error"
        error         str  (only on error)
    """
    cache_key = _extract_cache_key(url, prompt)

    # ── L1: extraction cache ─────────────────────────────────────────────────
    if not force_refresh:
        cached = await cache.get(cache_key)
        if cached:
            cached["cache_hit"] = True
            logger.info("Extract cache HIT  %s", url)
            return cached

    # ── L2: crawl ────────────────────────────────────────────────────────────
    crawl_result = await crawl_single(url)
    if crawl_result["status"] == "error":
        return {
            "url": url,
            "prompt": prompt,
            "data": {},
            "cache_hit": False,
            "chunks_used": 0,
            "total_chunks": 0,
            "status": "error",
            "error": crawl_result.get("error", "crawl_failed"),
        }

    raw_text = crawl_result.get("text", "")

    # ── L3: chunk ────────────────────────────────────────────────────────────
    chunks = chunk_text(raw_text)
    relevant_chunks = select_top_chunks(chunks, query=prompt)
    llm_context = "\n\n---\n\n".join(relevant_chunks)

    logger.info(
        "Extract pipeline | url=%s chunks=%d/%d llm_chars=%d",
        url, len(relevant_chunks), len(chunks), len(llm_context),
    )

    # ── L4: LLM extract ──────────────────────────────────────────────────────
    extracted = await extract_structured(
        page_text=llm_context,
        prompt=prompt,
        schema=schema,
        extra_context=extra_context,
    )

    # ── L5: Pydantic validation (optional) ───────────────────────────────────
    validated_data = extracted
    validation_error: Optional[str] = None

    if schema is not None and "_error" not in extracted:
        try:
            instance = schema.model_validate(extracted)
            validated_data = instance.model_dump()
        except ValidationError as ve:
            validation_error = str(ve)
            logger.warning("Schema validation failed: %s", validation_error)
            validated_data = extracted  # still return raw LLM output

    has_error = "_error" in extracted or validation_error is not None
    result: dict[str, Any] = {
        "url": url,
        "prompt": prompt,
        "data": validated_data,
        "cache_hit": False,
        "chunks_used": len(relevant_chunks),
        "total_chunks": len(chunks),
        "status": "ok" if not has_error else "partial",
    }
    if validation_error:
        result["validation_error"] = validation_error

    # ── L6: cache extraction result ──────────────────────────────────────────
    if result["status"] in ("ok", "partial"):
        await cache.set(cache_key, result)

    return result


# ---------------------------------------------------------------------------
# Convenience: extract a financial quote (wraps extract_from_page with
# a standard finance prompt so callers don't have to craft their own)
# ---------------------------------------------------------------------------

_QUOTE_PROMPT = (
    "Extract the following stock market data fields: "
    "regularMarketPrice (current price), "
    "regularMarketChangePercent (change percent today), "
    "regularMarketVolume (volume), "
    "fiftyTwoWeekHigh, fiftyTwoWeekLow, "
    "trailingPE (P/E ratio), "
    "marketCap, "
    "shortName (company name). "
    "Return null for any field not found."
)


async def extract_quote(ticker: str, force_refresh: bool = False) -> dict[str, Any]:
    """
    High-level helper: scrape + LLM-extract a Yahoo Finance quote page.
    Returns a typed dict with standard financial fields.
    """
    url = f"https://finance.yahoo.com/quote/{ticker.upper()}"
    return await extract_from_page(
        url=url,
        prompt=_QUOTE_PROMPT,
        force_refresh=force_refresh,
        extra_context=f"Ticker symbol: {ticker.upper()}",
    )
