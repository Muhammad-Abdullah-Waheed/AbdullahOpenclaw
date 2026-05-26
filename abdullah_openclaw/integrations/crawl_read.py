"""Optional Crawl4AI + Playwright rendering (heavy dependency).

Install: ``uv pip install 'abdullah-openclaw[crawl]'`` or ``uv pip install crawl4ai``,
then run Playwright browser install if the package requests it.

Used when ``WEBFETCH_RENDERER=crawl4ai``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("abdullah_openclaw.crawl")


def crawl4ai_available() -> bool:
    try:
        import crawl4ai  # noqa: F401

        return True
    except ImportError:
        return False


def fetch_markdown(url: str, *, timeout: float) -> str:
    """Return markdown page text or raise ``RuntimeError``."""
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError as e:
        raise RuntimeError(
            "crawl4ai is not installed. Install with: uv pip install 'abdullah-openclaw[crawl]' "
            f"({e})"
        ) from e

    async def _run() -> dict[str, Any]:
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await asyncio.wait_for(crawler.arun(url=url), timeout=timeout)
            if not result.success:
                raise RuntimeError(result.error_message or "Crawl4AI failed")
            title = ""
            if result.metadata and isinstance(result.metadata, dict):
                title = str(result.metadata.get("title") or "")
            md = (result.markdown or "").strip()
            return {"title": title, "markdown": md}

    # CLI: no running loop
    out = asyncio.run(_run())
    title = out["title"]
    md = out["markdown"]
    head = f"URL: {url}\n"
    if title:
        head += f"Title: {title}\n"
    head += "\n(rendered via Crawl4AI)\n\n"
    return head + md
