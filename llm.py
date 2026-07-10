# llm.py
"""
LLM client via OpenAI-compatible APIs.

Cascade (same order as TradeTalk ``LLMClient``):
  1. NVIDIA Build
  2. OpenRouter
  3. GitHub Models
  4. Gemini (OpenAI-compat endpoint)

All external LLM calls in FinCrawler go through this module so that
the model/provider can be swapped in a single place.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional, Type

from dotenv import load_dotenv
load_dotenv()

from openai import APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider cascade — NVIDIA → OpenRouter → GitHub Models → Gemini
# ---------------------------------------------------------------------------
_llm_semaphore = asyncio.Semaphore(1)
_clients: dict[str, AsyncOpenAI] = {}


@dataclass(frozen=True)
class _Provider:
    name: str
    client: AsyncOpenAI
    model: str
    max_tokens: int


def _reset_llm_clients() -> None:
    """Test helper — clear cached OpenAI clients."""
    _clients.clear()


def _client_for(name: str, api_key: str, base_url: str) -> AsyncOpenAI:
    key = f"{name}|{base_url}|{api_key[:8]}"
    if key not in _clients:
        _clients[key] = AsyncOpenAI(api_key=api_key, base_url=base_url.rstrip("/"))
        logger.info("LLM %s client initialised (base_url=%s)", name, base_url)
    return _clients[key]


def _openrouter_key() -> str:
    return (
        os.getenv("LLM_API_KEY", "").strip()
        or os.getenv("OPENROUTER_KEY", "").strip()
        or os.getenv("OPENROUTER_API_KEY", "").strip()
    )


def _nvidia_key() -> str:
    return (
        os.getenv("LLM_FALLBACK_API_KEY", "").strip()
        or os.getenv("NVIDIA_API_KEY", "").strip()
    )


def _github_key() -> str:
    return (
        os.getenv("GITHUB_MODELS_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )


def _gemini_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
_MODEL = os.getenv("LLM_MODEL", _DEFAULT_MODEL)
# Same-provider retry model (OpenRouter); provider fallback uses NVIDIA vars below.
_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", _DEFAULT_MODEL)
_FALLBACK_PROVIDER_MODEL = os.getenv("LLM_FALLBACK_PROVIDER_MODEL", "minimaxai/minimax-m3")
_FALLBACK_VISION_MODEL = os.getenv(
    "LLM_FALLBACK_VISION_MODEL", _FALLBACK_PROVIDER_MODEL
)
_FALLBACK_MAX_TOKENS = int(os.getenv("LLM_FALLBACK_MAX_TOKENS", "8192"))
_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16384"))
_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))  # low for factual extraction

_GITHUB_MODELS_BASE_URL = os.getenv(
    "GITHUB_MODELS_BASE_URL", "https://models.github.ai/inference"
).rstrip("/")
_GITHUB_MODELS_MODEL = os.getenv("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini")
_GEMINI_BASE_URL = os.getenv(
    "GEMINI_OPENAI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
).rstrip("/") + "/"
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


def _ordered_providers(*, vision: bool = False) -> list[_Provider]:
    """
    Build the try-order matching TradeTalk LLMClient:
    NVIDIA → OpenRouter → GitHub Models → Gemini.
    """
    providers: list[_Provider] = []

    nv_key = _nvidia_key()
    if nv_key:
        base = os.getenv(
            "LLM_FALLBACK_BASE_URL", "https://integrate.api.nvidia.com/v1"
        ).strip()
        model = _FALLBACK_VISION_MODEL if vision else _FALLBACK_PROVIDER_MODEL
        providers.append(
            _Provider(
                "nvidia",
                _client_for("nvidia", nv_key, base),
                model,
                _FALLBACK_MAX_TOKENS,
            )
        )

    or_key = _openrouter_key()
    if or_key:
        base = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
        model = (
            (_resolve_vision_model() if vision else _MODEL)
        )
        providers.append(
            _Provider(
                "openrouter",
                _client_for("openrouter", or_key, base),
                model,
                _MAX_TOKENS,
            )
        )

    gh_key = _github_key()
    if gh_key:
        providers.append(
            _Provider(
                "github",
                _client_for("github", gh_key, _GITHUB_MODELS_BASE_URL),
                _GITHUB_MODELS_MODEL,
                min(_MAX_TOKENS, 4096),
            )
        )

    gem_key = _gemini_key()
    if gem_key:
        providers.append(
            _Provider(
                "gemini",
                _client_for("gemini", gem_key, _GEMINI_BASE_URL),
                _GEMINI_MODEL,
                _MAX_TOKENS,
            )
        )

    return providers


def _get_primary_client() -> AsyncOpenAI:
    """Backward-compatible: first configured provider client (raises if none)."""
    providers = _ordered_providers()
    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set NVIDIA_API_KEY, LLM_API_KEY / "
            "OPENROUTER_API_KEY, GITHUB_MODELS_TOKEN / GITHUB_TOKEN, or GEMINI_API_KEY."
        )
    return providers[0].client


def _get_fallback_client() -> Optional[AsyncOpenAI]:
    """Backward-compatible: second provider client if present."""
    providers = _ordered_providers()
    if len(providers) < 2:
        return None
    return providers[1].client


def _get_client() -> AsyncOpenAI:
    """Backward-compatible alias for primary client."""
    return _get_primary_client()


def _should_use_nvidia_fallback(exc: BaseException) -> bool:
    """True when a provider error should trigger cascade to the next provider."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (401, 402, 403, 429, 500, 502, 503)
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "payment required",
            "insufficient credits",
            "requires more credits",
            "unauthorized",
            "forbidden",
            "temporarily unavailable",
        )
    )


# Alias used by tests / callers that still say "nvidia fallback"
_should_failover = _should_use_nvidia_fallback


async def _create_chat_completion(
    *,
    messages: list,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    vision: bool = False,
):
    """
    Try providers in order: NVIDIA → OpenRouter → GitHub Models → Gemini.
    ``model`` overrides the first matching OpenRouter model when provided.
    """
    temperature = _TEMPERATURE if temperature is None else temperature
    providers = _ordered_providers(vision=vision)
    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set NVIDIA_API_KEY, OPENROUTER_API_KEY / "
            "LLM_API_KEY, GITHUB_MODELS_TOKEN, or GEMINI_API_KEY."
        )

    last_exc: BaseException | None = None
    for idx, prov in enumerate(providers):
        use_model = prov.model
        # Honor explicit model override on OpenRouter slot
        if model and prov.name == "openrouter":
            use_model = model
        use_max = max_tokens if max_tokens is not None else prov.max_tokens
        try:
            async with _llm_semaphore:
                resp = await prov.client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=use_max,
                    stream=False,
                )
            if idx > 0:
                logger.info(
                    "LLM cascade succeeded on provider=%s model=%s (after %d prior failure(s))",
                    prov.name,
                    use_model,
                    idx,
                )
            return resp
        except Exception as exc:
            last_exc = exc
            has_next = idx < len(providers) - 1
            if has_next and _should_use_nvidia_fallback(exc):
                logger.warning(
                    "LLM provider=%s model=%s failed (%s) — trying next in cascade",
                    prov.name,
                    use_model,
                    exc,
                )
                continue
            if has_next:
                # Soft-fail other errors too so GitHub/Gemini still get a chance
                logger.warning(
                    "LLM provider=%s model=%s failed (%s) — trying next in cascade",
                    prov.name,
                    use_model,
                    exc,
                )
                continue
            raise

    assert last_exc is not None
    raise last_exc


def _resolve_vision_model() -> str:
    """Vision/screenshot extraction model — defaults to LLM_MODEL."""
    return (
        os.getenv("VISION_LLM_MODEL", "").strip()
        or os.getenv("LLM_VISION_MODEL", "").strip()
        or os.getenv("LLM_MODEL", "").strip()
        or _MODEL
    )


# ---------------------------------------------------------------------------
# Core extraction helper
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a precise financial data extraction assistant.
Given a web page's text content, extract the requested information accurately.
Return ONLY a valid JSON object matching the user's schema.
Never invent data. If a field cannot be found, use null.
Do not include markdown fences, explanations, or any text outside the JSON object."""

_VISION_SYSTEM_PROMPT = """You are a precise financial data extraction assistant reading web page screenshots.
Extract only values clearly visible in the images.
Return ONLY one valid JSON object — no markdown fences, no commentary, no reasoning text, no preamble.
Never invent data. Use null for fields not visible.
Your entire reply must be parseable as JSON starting with { and ending with }."""

_SHOP_SYSTEM_PROMPT = """You are a precise e-commerce data extraction assistant.
Given retail search page text, extract product and price information accurately.
Return ONLY a single valid JSON object — no markdown fences, no commentary.
Never invent prices. Use null for missing fields.
If pre-detected price candidates are provided, pick the one that matches the requested product.
The price field must be a number (USD), not a string like "$419.00"."""


def _message_text(message) -> str:
    """Collect LLM output; reasoning models (e.g. Nemotron) may use alternate fields."""
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


def _message_candidates_for_json(message) -> list[str]:
    """
    Candidate strings to parse as JSON.

    Reasoning models often leave ``content`` empty and put chain-of-thought in
    ``reasoning`` while the JSON (if any) may live in either field.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(val: str | None) -> None:
        text = (val or "").strip()
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    _add(getattr(message, "content", None))
    for attr in ("reasoning", "reasoning_content"):
        _add(getattr(message, attr, None))
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
            _add("\n".join(parts))
    return candidates


async def _create_vision_chat_completion(
    *,
    messages: list,
    model: str,
    use_nvidia_fallback: bool = False,
):
    """Vision chat completion via the full provider cascade."""
    # ``use_nvidia_fallback`` kept for call-site compat; cascade already includes NVIDIA+.
    _ = use_nvidia_fallback
    return await _create_chat_completion(
        messages=messages,
        model=model,
        vision=True,
    )


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

    model = _resolve_vision_model()

    batch_size = int(os.getenv("VISION_SCREENSHOTS_PER_BATCH", "6"))
    if len(images) <= batch_size:
        batches = [images]
        batch_offsets = [0]
    else:
        batches = [images[i : i + batch_size] for i in range(0, len(images), batch_size)]
        batch_offsets = list(range(0, len(images), batch_size))

    system_content = _SHOP_SYSTEM_PROMPT if task == "shopping" else _VISION_SYSTEM_PROMPT
    if extra_context:
        system_content += f"\n\nAdditional context: {extra_context}"
    system_content += (
        "\n\nYou are reading sequential viewport screenshots of the same web page "
        "scrolled from top to bottom. Merge all visible data into one JSON object. "
        "Extract only values clearly visible. Reply with JSON only — no other text."
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
                    "Combine data from all images; do not duplicate list items. "
                    "Respond with a single JSON object only."
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

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        parsed: dict | None = None
        last_raw = ""
        for attempt, use_fb in enumerate((False, True)):
            if attempt == 1 and _get_fallback_client() is None:
                break
            try:
                response = await _create_vision_chat_completion(
                    messages=messages,
                    model=model,
                    use_nvidia_fallback=use_fb,
                )
                message = response.choices[0].message
                candidates = _message_candidates_for_json(message)
                last_raw = candidates[0] if candidates else _message_text(message)
                for candidate in candidates or [last_raw]:
                    parsed = _parse_json_response(candidate)
                    if not parsed.get("_error"):
                        last_raw = candidate
                        break
                logger.info(
                    "Vision LLM extract | model=%s panels=%s attempt=%d response_chars=%d parsed=%s",
                    _FALLBACK_VISION_MODEL if use_fb else model,
                    panel_range,
                    attempt + 1,
                    len(last_raw),
                    "ok" if parsed and not parsed.get("_error") else "failed",
                )
                if parsed and not parsed.get("_error"):
                    break
                if attempt == 0 and parsed and parsed.get("_error") == "json_parse_failed":
                    logger.warning(
                        "Vision JSON parse failed for %s — retrying with NVIDIA fallback",
                        panel_range,
                    )
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 0 and _get_fallback_client() is not None:
                    logger.warning(
                        "Vision LLM failed for %s (%s) — retrying with NVIDIA fallback",
                        panel_range,
                        exc,
                    )
                    continue
                logger.exception("Vision LLM extraction failed for %s", panel_range)
                parsed = {"_error": str(exc), "_llm_raw": None}
                break

        partials.append(parsed or {"_error": "json_parse_failed", "_llm_raw": last_raw})

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
        current_model = model if attempt == 0 else _FALLBACK_MODEL

        logger.info(
            "LLM extract | model=%s attempt=%d/%d prompt_chars=%d",
            current_model,
            attempt + 1,
            max_retries,
            len(user_content),
        )

        try:
            response = await _create_chat_completion(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                model=current_model,
                vision=False,
            )

            raw = _message_text(response.choices[0].message)
            logger.info("LLM raw response: %s", raw[:500])
            return _parse_json_response(raw)

        except RateLimitError as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "LLM %s rate limited, retrying in %ds (attempt %d/%d)",
                    current_model,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
            else:
                logger.exception("LLM extraction failed after max retries (Rate Limit)")
                return {"_error": str(exc), "_llm_raw": None}
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM extraction failed")
            return {"_error": str(exc), "_llm_raw": None}



def _extract_json_objects(text: str) -> list[str]:
    """Find balanced {...} substrings in *text*, largest first."""
    if not text or "{" not in text:
        return []
    found: list[str] = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        start = i
        for j in range(i, len(text)):
            ch = text[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[start : j + 1])
                    i = j + 1
                    break
        else:
            i += 1
    found.sort(key=len, reverse=True)
    return found


def _parse_json_response(raw: str) -> dict:
    """
    Robustly parse JSON from LLM output.
    Handles markdown fences, leading text, trailing garbage, reasoning prose.
    """
    if not raw or not raw.strip():
        return {"_error": "json_parse_failed", "_llm_raw": raw}

    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Fix common trailing commas before array/object closing brackets
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    attempts = [cleaned]
    attempts.extend(_extract_json_objects(cleaned))

    for candidate in attempts:
        if not candidate:
            continue
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
            return {"result": result}
        except json.JSONDecodeError:
            continue

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
    for key in ("product_name", "title", "availability", "seller", "company_name", "ticker"):
        m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            out[key] = m.group(1).replace('\\"', '"')
    for key in ("price", "original_price", "rating", "review_count", "regularMarketPrice"):
        m = re.search(rf'"{re.escape(key)}"\s*:\s*([\d.]+)', raw)
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    # Nested quote_header.regularMarketPrice
    m = re.search(
        r'"quote_header"\s*:\s*\{([^}]*)',
        raw,
        re.DOTALL,
    )
    if m:
        block = m.group(1)
        qh: dict = {}
        tm = re.search(r'"ticker"\s*:\s*"([^"]+)"', block)
        if tm:
            qh["ticker"] = tm.group(1)
        pm = re.search(r'"regularMarketPrice"\s*:\s*([\d.]+)', block)
        if pm:
            qh["regularMarketPrice"] = float(pm.group(1))
        if qh:
            out["quote_header"] = qh
    if out.get("price") or out.get("product_name") or out.get("title"):
        return out
    if out.get("quote_header"):
        return out
    if out.get("regularMarketPrice") or out.get("ticker"):
        qh = {
            k: out[k]
            for k in ("regularMarketPrice", "ticker", "company_name")
            if out.get(k) is not None
        }
        return {"quote_header": qh}
    return None


# ---------------------------------------------------------------------------
# Simple health-check ping (used by /health endpoint)
# ---------------------------------------------------------------------------

async def llm_health_check() -> bool:
    """Returns True if any cascade provider (NVIDIA→OpenRouter→GitHub→Gemini) is reachable."""
    messages = [{"role": "user", "content": "Reply with the single word: ok"}]
    try:
        resp = await _create_chat_completion(
            messages=messages,
            model=_MODEL,
            max_tokens=25,
            temperature=0,
        )
        return len(resp.choices) > 0 and (
            bool(resp.choices[0].message.content)
            or hasattr(resp.choices[0].message, "reasoning")
        )
    except Exception:
        return False
