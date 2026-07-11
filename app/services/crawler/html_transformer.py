"""HTML → LLM-ready formats (markdown, links, main content) — AnyCrawl-style."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse


_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+\n")
_BLANK = re.compile(r"\n{3,}")


class _LinkCollector(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        abs_url = urljoin(self.base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            return
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        if clean not in self._seen:
            self._seen.add(clean)
            self.links.append(clean)


def strip_noise_html(html: str, *, exclude_tags: Iterable[str] | None = None) -> str:
    cleaned = _SCRIPT_STYLE.sub("", html or "")
    for tag in exclude_tags or ():
        cleaned = re.sub(
            rf"<{re.escape(tag)}[^>]*>.*?</{re.escape(tag)}>",
            "",
            cleaned,
            flags=re.I | re.S,
        )
    return cleaned


def extract_main_content(html: str) -> str:
    """Prefer <main>/<article>; fall back to <body> with chrome stripped."""
    for pattern in (
        r"<main[^>]*>(.*?)</main>",
        r"<article[^>]*>(.*?)</article>",
        r'<(?:div|section)[^>]+(?:id|class)=["\'][^"\']*(?:content|main|article)[^"\']*["\'][^>]*>(.*?)</(?:div|section)>',
    ):
        m = re.search(pattern, html or "", re.I | re.S)
        if m and len(m.group(1)) > 200:
            return m.group(1)
    body = re.search(r"<body[^>]*>(.*?)</body>", html or "", re.I | re.S)
    chunk = body.group(1) if body else (html or "")
    # Drop obvious chrome
    for chrome in ("header", "footer", "nav", "aside"):
        chunk = re.sub(rf"<{chrome}[^>]*>.*?</{chrome}>", "", chunk, flags=re.I | re.S)
    return chunk


def html_to_text(html: str, max_chars: int = 200_000) -> str:
    text = _TAG.sub(" ", strip_noise_html(html))
    text = re.sub(r"[ \t]+", " ", text)
    text = _BLANK.sub("\n\n", text.replace("\r", ""))
    return text.strip()[:max_chars]


def html_to_markdown(
    html: str,
    *,
    only_main_content: bool = True,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    max_chars: int = 200_000,
) -> str:
    chunk = extract_main_content(html) if only_main_content else (html or "")
    chunk = strip_noise_html(chunk, exclude_tags=exclude_tags)

    if include_tags:
        parts: list[str] = []
        for tag in include_tags:
            for m in re.finditer(
                rf"<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>",
                chunk,
                re.I | re.S,
            ):
                parts.append(m.group(0))
        if parts:
            chunk = "\n".join(parts)

    # Lightweight structural markdown
    chunk = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", chunk, flags=re.I | re.S)
    chunk = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", chunk, flags=re.I | re.S)
    chunk = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", chunk, flags=re.I | re.S)
    chunk = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", chunk, flags=re.I | re.S)
    chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
    chunk = re.sub(r"</p>", "\n\n", chunk, flags=re.I)
    chunk = re.sub(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        chunk,
        flags=re.I | re.S,
    )
    text = html_to_text(chunk, max_chars=max_chars)
    return _WS.sub("\n", text).strip()


def extract_links(html: str, base_url: str) -> list[str]:
    collector = _LinkCollector(base_url)
    try:
        collector.feed(html or "")
        collector.close()
    except Exception:
        # Fallback regex
        links: list[str] = []
        seen: set[str] = set()
        for m in re.finditer(r'href=["\']([^"\']+)["\']', html or "", re.I):
            abs_url = urljoin(base_url, m.group(1))
            if abs_url.startswith("http") and abs_url not in seen:
                seen.add(abs_url)
                links.append(abs_url)
        return links
    return collector.links


def transform_page(
    html: str,
    *,
    base_url: str,
    formats: list[str] | None = None,
    only_main_content: bool = True,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> dict:
    """Build Firecrawl/AnyCrawl-style format payload from HTML."""
    fmts = formats or ["markdown"]
    out: dict = {}
    if "rawHtml" in fmts or "raw_html" in fmts:
        out["rawHtml"] = html
    if "html" in fmts:
        out["html"] = extract_main_content(html) if only_main_content else html
    if "markdown" in fmts:
        out["markdown"] = html_to_markdown(
            html,
            only_main_content=only_main_content,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
        )
    if "text" in fmts:
        src = extract_main_content(html) if only_main_content else html
        out["text"] = html_to_text(src)
    if "links" in fmts:
        out["links"] = extract_links(html, base_url)
    # Always useful for callers that expect content alias
    if "markdown" in out and "content" not in out:
        out["content"] = out["markdown"]
    return out
