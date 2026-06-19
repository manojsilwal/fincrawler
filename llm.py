# llm.py
"""
LLM client wrapping the NVIDIA-hosted DeepSeek v4 Pro model
via the OpenAI-compatible API.

All external LLM calls in FinCrawler go through this module so that
the model/provider can be swapped in a single place.
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional, Type

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------
_client: Optional[AsyncOpenAI] = None
_llm_semaphore = asyncio.Semaphore(1)  # Prevent NVIDIA API 429 Too Many Requests


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("LLM_API_KEY", "") or os.getenv("OPENROUTER_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
        if not api_key:
            raise RuntimeError(
                "LLM_API_KEY environment variable is not set. "
                "Add it to your .env file."
            )
        _client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info("LLM client initialised (base_url=%s)", base_url)
    return _client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MODEL = os.getenv("LLM_MODEL", "deepseek-ai/deepseek-v4-pro")
_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "deepseek-ai/deepseek-v4-flash")
_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16384"))
_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))  # low for factual extraction


# ---------------------------------------------------------------------------
# Core extraction helper
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a precise financial data extraction assistant.
Given a web page's text content, extract the requested information accurately.
Return ONLY a valid JSON object matching the user's schema.
Never invent data. If a field cannot be found, use null.
Do not include markdown fences, explanations, or any text outside the JSON object."""

_SHOP_SYSTEM_PROMPT = """You are a precise e-commerce data extraction assistant.
Given retail search page text, extract product and price information accurately.
Return ONLY a single valid JSON object — no markdown fences, no commentary.
Never invent prices. Use null for missing fields.
If pre-detected price candidates are provided, pick the one that matches the requested product.
The price field must be a number (USD), not a string like "$419.00"."""


def _message_text(message) -> str:
    """Collect LLM output; reasoning models (e.g. minimax-m3) may use alternate fields."""
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content
    for attr in ("reasoning", "reasoning_content"):
        val = getattr(message, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    details = getattr(message, "reasoning_details", None)
    if isinstance(details, list):
        parts = []
        for item in details:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    return content


async def extract_from_screenshot(
    image_png: bytes,
    prompt: str,
    *,
    extra_context: Optional[str] = None,
    task: str = "finance",
) -> dict:
    """Vision fallback for a single screenshot."""
    return await extract_from_screenshots(
        [image_png],
        prompt,
        extra_context=extra_context,
        task=task,
    )


def _merge_vision_dicts(parts: list[dict]) -> dict:
    """Merge partial vision extractions; first non-null wins, lists are deduped."""
    merged: dict = {}
    for part in parts:
        if not part or part.get("_error"):
            continue
        for key, val in part.items():
            if str(key).startswith("_") or val is None:
                continue
            if key not in merged or merged[key] is None:
                merged[key] = val
                continue
            existing = merged[key]
            if isinstance(val, list) and isinstance(existing, list):
                seen: set[str] = set()
                combined: list = []
                for item in existing + val:
                    sig = json.dumps(item, sort_keys=True, default=str) if isinstance(item, dict) else str(item)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    combined.append(item)
                merged[key] = combined
            elif isinstance(val, dict) and isinstance(existing, dict):
                merged[key] = _merge_vision_dicts([existing, val])
    return merged


async def extract_from_screenshots(
    images: list[bytes],
    prompt: str,
    *,
    extra_context: Optional[str] = None,
    task: str = "finance",
) -> dict:
    """
    Vision fallback: extract structured data from one or more page screenshots.
    Multiple images should be sequential viewport panels (top-to-bottom scroll).
    """
    import base64

    if not images:
        return {"_error": "no screenshots provided", "_llm_raw": None}

    client = _get_client()
    model = os.getenv("VISION_LLM_MODEL", "") or os.getenv("LLM_VISION_MODEL", "")
    if not model:
        model = os.getenv("LLM_MODEL", "") or _MODEL
        if os.getenv("LLM_BASE_URL", "").find("openrouter") >= 0 and model == _MODEL:
            model = "google/gemini-3.5-flash"

    batch_size = int(os.getenv("VISION_SCREENSHOTS_PER_BATCH", "6"))
    if len(images) <= batch_size:
        batches = [images]
        batch_offsets = [0]
    else:
        batches = [images[i : i + batch_size] for i in range(0, len(images), batch_size)]
        batch_offsets = list(range(0, len(images), batch_size))

    system_content = _SHOP_SYSTEM_PROMPT if task == "shopping" else _SYSTEM_PROMPT
    if extra_context:
        system_content += f"\n\nAdditional context: {extra_context}"
    system_content += (
        "\n\nYou are reading sequential viewport screenshots of the same web page "
        "scrolled from top to bottom. Merge all visible data into one JSON object. "
        "Extract only values clearly visible. Return JSON only."
    )

    partials: list[dict] = []
    for batch_idx, batch in enumerate(batches):
        offset = batch_offsets[batch_idx]
        panel_range = f"panels {offset + 1}-{offset + len(batch)} of {len(images)}"
        user_content: list[dict] = [
            {
                "type": "text",
                "text": (
                    f"Instruction: {prompt}\n\n"
                    f"These are {panel_range} (top to bottom). "
                    "Combine data from all images; do not duplicate list items."
                ),
            },
        ]
        for i, img in enumerate(batch):
            b64 = base64.standard_b64encode(img).decode("ascii")
            user_content.append(
                {
                    "type": "text",
                    "text": f"Screenshot panel {offset + i + 1}/{len(images)}:",
                }
            )
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )

        try:
            async with _llm_semaphore:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                )
            raw = _message_text(response.choices[0].message)
            logger.info(
                "Vision LLM extract | model=%s panels=%s response_chars=%d",
                model,
                panel_range,
                len(raw),
            )
            partials.append(_parse_json_response(raw))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vision LLM extraction failed for %s", panel_range)
            partials.append({"_error": str(exc), "_llm_raw": None})

    if len(partials) == 1:
        return partials[0]
    merged = _merge_vision_dicts(partials)
    errors = [p.get("_error") for p in partials if p.get("_error")]
    if errors and not merged:
        merged["_error"] = "; ".join(str(e) for e in errors)
    return merged


async def extract_structured(
    page_text: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    extra_context: Optional[str] = None,
    task: str = "finance",
) -> dict:
    """
    Send ``page_text`` + ``prompt`` to the LLM and parse the JSON response.

    Parameters
    ----------
    page_text:
        The (already chunked/retrieved) page content.
    prompt:
        Natural language instruction, e.g. "Extract current price, P/E ratio".
    schema:
        Optional Pydantic model class.  Its JSON schema is included in the
        system prompt so the model knows exactly which fields to fill.
    extra_context:
        Any additional system-level context to inject (e.g. ticker symbol).

    Returns
    -------
    dict — always a dict; may contain an ``_error`` key on failure.
    """
    client = _get_client()
    model = _MODEL

    # Build the schema hint
    schema_hint = ""
    if schema is not None:
        try:
            schema_hint = (
                "\n\nTarget JSON schema:\n"
                + json.dumps(schema.model_json_schema(), indent=2)
            )
        except Exception:
            pass

    system_content = _SHOP_SYSTEM_PROMPT if task == "shopping" else _SYSTEM_PROMPT
    if extra_context:
        system_content += f"\n\nAdditional context: {extra_context}"
    if schema_hint:
        system_content += schema_hint

    user_content = (
        f"Instruction: {prompt}\n\n"
        f"Page content:\n{page_text}"
    )

    max_retries = 3
    base_delay = 8

    for attempt in range(max_retries):
        current_model = model
        # Use fallback on 2nd and 3rd attempt if we get rate limited
        if attempt > 0 and _FALLBACK_MODEL:
            current_model = _FALLBACK_MODEL

        logger.info("LLM extract | model=%s attempt=%d/%d prompt_chars=%d", current_model, attempt + 1, max_retries, len(user_content))

        try:
            async with _llm_semaphore:
                response = await client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                    stream=False,
                )

            raw = _message_text(response.choices[0].message)
            logger.info("LLM raw response: %s", raw[:500])
            return _parse_json_response(raw)

        except RateLimitError as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"LLM %s rate limited, retrying in %ds (attempt %d/%d)", current_model, delay, attempt + 1, max_retries)
                await asyncio.sleep(delay)
            else:
                logger.exception("LLM extraction failed after max retries (Rate Limit)")
                return {"_error": str(exc), "_llm_raw": None}
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM extraction failed")
            return {"_error": str(exc), "_llm_raw": None}



def _parse_json_response(raw: str) -> dict:
    """
    Robustly parse JSON from LLM output.
    Handles markdown fences, leading text, trailing garbage.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Fix common trailing commas before array/object closing brackets
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    # Try direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
        return {"result": result}
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object anywhere in the string
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    salvaged = _salvage_partial_json(cleaned)
    if salvaged:
        logger.info("Salvaged partial LLM JSON fields: %s", list(salvaged.keys()))
        return salvaged

    logger.warning("Could not parse LLM response as JSON: %s…", raw[:200])
    return {"_error": "json_parse_failed", "_llm_raw": raw}


def _salvage_partial_json(raw: str) -> dict | None:
    """Recover key fields when the model returns truncated but mostly-valid JSON."""
    if not raw or "{" not in raw:
        return None
    out: dict = {}
    for key in ("product_name", "title", "availability", "seller"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            out[key] = m.group(1).replace('\\"', '"')
    for key in ("price", "original_price", "rating", "review_count"):
        m = re.search(rf'"{key}"\s*:\s*([\d.]+)', raw)
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    if out.get("price") or out.get("product_name") or out.get("title"):
        return out
    return None


# ---------------------------------------------------------------------------
# Simple health-check ping (used by /health endpoint)
# ---------------------------------------------------------------------------

async def llm_health_check() -> bool:
    """Returns True if the LLM endpoint is reachable."""
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=25,
            temperature=0,
        )
        # Handle reasoning models where content might be empty but reasoning/choices exist
        return len(resp.choices) > 0 and (bool(resp.choices[0].message.content) or hasattr(resp.choices[0].message, 'reasoning'))
    except Exception:
        return False
