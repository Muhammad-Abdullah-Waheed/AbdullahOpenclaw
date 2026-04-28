"""Web search via Tavily (Stage 7+).

Requires ``TAVILY_API_KEY`` (https://tavily.com). Billing/credits apply per Tavily’s plan.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("abdullah_openclaw.search")


def web_search_enabled() -> bool:
    raw = (os.environ.get("WEBSEARCH_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def handle_web_search(args: dict[str, Any]) -> str:
    if not web_search_enabled():
        return "ERROR: web_search is disabled (WEBSEARCH_ENABLED=0)"

    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return (
            "ERROR: TAVILY_API_KEY is not set. Get a key at https://tavily.com and add it to .env"
        )

    query = str(args.get("query", "")).strip()
    if not query:
        return "ERROR: missing query"

    max_results = _env_int("TAVILY_MAX_RESULTS", 5)
    mr_arg = args.get("max_results")
    if mr_arg is not None:
        try:
            max_results = max(1, min(20, int(mr_arg)))
        except (TypeError, ValueError):
            pass

    depth = str(args.get("search_depth") or os.environ.get("TAVILY_SEARCH_DEPTH") or "basic").strip()
    if depth not in {"basic", "advanced", "fast", "ultra-fast"}:
        depth = "basic"

    try:
        from tavily import TavilyClient
    except ImportError as e:
        return f"ERROR: tavily-python not installed ({e}); run: uv pip install tavily-python"

    logger.info("web_search: q=%r depth=%s max=%s", query[:120], depth, max_results)

    try:
        client = TavilyClient(api_key=key)
        resp = client.search(query=query, search_depth=depth, max_results=max_results)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Tavily request failed: {type(e).__name__}: {e}"

    results = resp.get("results") if isinstance(resp, dict) else None
    if not results:
        ans = resp.get("answer") if isinstance(resp, dict) else None
        if ans:
            return f"Tavily (no result list; answer field):\n{ans}"
        return "No results from Tavily."

    lines: list[str] = []
    answer = resp.get("answer") if isinstance(resp, dict) else None
    if answer:
        lines.append(f"Summary: {answer}\n")

    for i, r in enumerate(results, 1):
        title = str(r.get("title") or "").strip()
        url = str(r.get("url") or "").strip()
        score = r.get("score")
        snippet = str(r.get("content") or r.get("snippet") or "").strip()
        sc = f" [{float(score):.2f}]" if isinstance(score, (int, float)) else ""
        lines.append(f"{i}. {title}{sc}\n   {url}\n   {snippet[:500]}")

    return "\n\n".join(lines)


WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web via Tavily for current events, documentation, or facts. "
            "Returns titles, URLs, and snippets. Requires TAVILY_API_KEY. "
            "Prefer this for discovery; use fetch_url to read a specific URL in depth."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."},
                "max_results": {
                    "type": "integer",
                    "description": "1–20 (default 5).",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced", "fast", "ultra-fast"],
                    "description": "Tavily depth tradeoff (credits differ). Default basic.",
                },
            },
            "required": ["query"],
        },
    },
}
