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


async def extract_structured(
    page_text: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    extra_context: Optional[str] = None,
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

    system_content = _SYSTEM_PROMPT
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

            raw = response.choices[0].message.content or ""
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

    logger.warning("Could not parse LLM response as JSON: %s…", raw[:200])
    return {"_error": "json_parse_failed", "_llm_raw": raw}


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
