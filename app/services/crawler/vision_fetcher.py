"""Generic scroll + screenshot + vision LLM extraction for all FinCrawler flows."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from llm import extract_from_screenshots

logger = logging.getLogger(__name__)

_SHOPPING_VISION_PROMPT = """Extract shopping/product information visible on this retail page.
Return JSON with either:
- A single product: product_name, price (float USD), original_price, availability, seller, rating, review_count, product_url
- OR a search results page: products array of those fields for each visible listing (max 10)
Ignore accessories and sponsored noise. Use null for missing fields."""

_GENERIC_VISION_PROMPT = """Extract the main structured facts visible on this web page as JSON.
Include titles, prices, numbers, labels, tables, and key entities clearly shown in the screenshots.
Use null for fields not visible. Do not invent values."""


def vision_fallback_enabled() -> bool:
    return os.getenv("VISION_FALLBACK_ENABLED", "true").lower() not in ("0", "false", "no")


def flatten_vision_extracted(
    extracted: dict[str, Any],
    *,
    prefix: str = "vision",
    exclude_keys: tuple[str, ...] = ("news",),
) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, val in extracted.items():
        if str(key).startswith("_") or key in exclude_keys:
            continue
        if isinstance(val, dict):
            for sub_k, sub_v in val.items():
                if sub_v is not None:
                    flat[f"{prefix}.{key}.{sub_k}"] = sub_v
        else:
            flat[f"{prefix}.{key}"] = val
    return flat


def promote_vision_aliases(
    flat: dict[str, Any],
    extracted: dict[str, Any],
    aliases: list[tuple[str, str]],
) -> None:
    for src, dst in aliases:
        if dst in flat and flat.get(dst) is not None:
            continue
        if src in flat:
            flat[dst] = flat[src]
            continue
        if "." in src:
            section, field = src.split(".", 1)
            nested = extracted.get(section)
            if isinstance(nested, dict) and nested.get(field) is not None:
                flat[dst] = nested[field]


def shopping_vision_has_signal(data: dict[str, Any]) -> bool:
    if data.get("price") or data.get("product_name"):
        return True
    products = data.get("products")
    return isinstance(products, list) and len(products) > 0


def vision_flat_to_shopping_data(flat: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    """Map vision extraction to shop_service field names."""
    if extracted.get("products") and isinstance(extracted["products"], list) and extracted["products"]:
        row = dict(extracted["products"][0])
    else:
        row = {
            "product_name": flat.get("vision.product_name") or flat.get("vision.products.0.product_name"),
            "price": flat.get("vision.price") or flat.get("vision.products.0.price"),
            "original_price": flat.get("vision.original_price"),
            "availability": flat.get("vision.availability"),
            "seller": flat.get("vision.seller"),
            "rating": flat.get("vision.rating"),
            "review_count": flat.get("vision.review_count"),
            "product_url": flat.get("vision.product_url"),
        }
        for k, v in extracted.items():
            if not str(k).startswith("_") and k not in ("products", "news") and v is not None:
                if k not in row or row[k] is None:
                    row[k] = v

    if row.get("price") is not None:
        try:
            row["price"] = float(str(row["price"]).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            row["price"] = None
    row["price_source"] = "vision"
    return {k: v for k, v in row.items() if v is not None}


async def capture_screenshots_for_url(
    url: str,
    *,
    retailer_key: str = "",
) -> tuple[list[bytes], dict[str, Any]]:
    from app.config import get_settings
    from app.services.asp.profiles import get_retailer_profile
    from app.services.crawler.browser_pool import get_browser_pool
    from app.services.crawler.human_behavior import dismiss_consent, run_behavior
    from app.services.crawler.screenshot_capture import capture_scrolled_screenshots

    profile = get_retailer_profile(retailer_key)
    settings = get_settings()
    meta: dict[str, Any] = {"path": "screenshot_vision_scroll", "url": url}

    pool = await get_browser_pool(size=settings.browser_pool_size)
    async with pool.page(retailer_key=retailer_key or None) as (page, _ctx):
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_nav_timeout_ms)
        await page.wait_for_timeout(1500)
        await dismiss_consent(page, profile.get("consent_selectors", []))
        wait_sel = profile.get("wait_selector")
        if wait_sel:
            try:
                await page.wait_for_selector(wait_sel, timeout=10_000, state="visible")
            except Exception:
                pass
        await page.wait_for_timeout(int(profile.get("hydration_wait_ms") or 6000))
        await run_behavior(page)

        shots = await capture_scrolled_screenshots(page)
        meta["screenshot_count"] = len(shots)
        meta["screenshot_bytes"] = sum(s["bytes"] for s in shots)
        meta["screenshot_scroll_ys"] = [s["scroll_y"] for s in shots]
        images = [s["png"] for s in shots]

    return images, meta


async def vision_extract_page(
    url: str,
    *,
    retailer_key: str = "",
    prompt: str,
    task: str = "shopping",
    extra_context: str | None = None,
    field_prefix: str = "vision",
    exclude_keys: tuple[str, ...] = ("news",),
    field_aliases: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Scroll-capture screenshots of *url* and extract structured data via vision LLM.
    """
    images, meta = await capture_screenshots_for_url(url, retailer_key=retailer_key)
    if not images:
        return {
            "ok": False,
            "flat": {},
            "extracted": {"_error": "no_screenshots"},
            "source": "vision_screenshot_failed",
            "meta": meta,
        }

    extracted = await extract_from_screenshots(
        images,
        prompt,
        extra_context=(extra_context or "") + f" URL: {url}. {len(images)} scroll panels.",
        task=task,
    )
    flat = flatten_vision_extracted(extracted, prefix=field_prefix, exclude_keys=exclude_keys)
    if field_aliases:
        promote_vision_aliases(flat, extracted, field_aliases)

    ok = "_error" not in extracted and bool(flat)
    return {
        "ok": ok,
        "flat": flat,
        "extracted": extracted,
        "source": "vision_screenshot" if ok else "vision_screenshot_failed",
        "meta": {**meta, "vision_error": extracted.get("_error")},
    }


async def vision_extract_shopping(
    url: str,
    *,
    query: str,
    retailer_key: str,
    candidates: list[float] | None = None,
) -> dict[str, Any]:
    from shop_price_extract import merge_shop_extraction, retailer_prompt_hint

    hint = retailer_prompt_hint(retailer_key)
    prompt = f"{hint}\n\n{_SHOPPING_VISION_PROMPT}" if hint else _SHOPPING_VISION_PROMPT
    prompt = prompt.replace('"{query}"', f'"{query}"') if "{query}" in prompt else f'{prompt}\nSearch query: "{query}".'

    result = await vision_extract_page(
        url,
        retailer_key=retailer_key,
        prompt=prompt,
        task="shopping",
        extra_context=f"Retailer: {retailer_key}. Query: {query}.",
    )
    data = vision_flat_to_shopping_data(result.get("flat") or {}, result.get("extracted") or {})
    if candidates:
        data = merge_shop_extraction(data, candidates, query=query, retailer_key=retailer_key)
    result["data"] = data
    result["ok"] = shopping_vision_has_signal(data)
    return result


async def maybe_apply_vision_fallback(
    fetch_result: dict[str, Any],
    url: str,
    *,
    retailer_key: str = "",
    task: str = "shopping",
    prompt: str | None = None,
    is_sufficient: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """
    If fetch_result lacks usable structured/HTML signals, run scroll+vision extraction
    and attach vision fields to the envelope.
    """
    if not vision_fallback_enabled():
        return fetch_result

    if is_sufficient and is_sufficient(fetch_result):
        return fetch_result

    from app.services.asp.detector import is_usable_scrape

    if is_sufficient is None and is_usable_scrape(fetch_result, retailer_key):
        return fetch_result

    if fetch_result.get("vision_fallback"):
        return fetch_result

    logger.info("Running scroll+screenshot vision fallback for %s (%s)", url, retailer_key or "generic")
    use_prompt = prompt or (_SHOPPING_VISION_PROMPT if task == "shopping" else _GENERIC_VISION_PROMPT)
    vision = await vision_extract_page(
        url,
        retailer_key=retailer_key,
        prompt=use_prompt,
        task=task,
        extra_context=f"Retailer: {retailer_key or 'generic'}.",
    )

    fetch_result = dict(fetch_result)
    fetch_result["vision_fallback"] = True
    fetch_result["vision_meta"] = vision.get("meta") or {}
    fetch_result["vision_source"] = vision.get("source")
    if vision.get("flat"):
        fetch_result["vision_data"] = vision["flat"]
        fetch_result["vision_extracted"] = vision.get("extracted") or {}
        if vision.get("ok"):
            fetch_result["status"] = fetch_result.get("status") or "ok"
            fetch_result["fetch_backend"] = fetch_result.get("fetch_backend") or "vision_screenshot"
    return fetch_result
