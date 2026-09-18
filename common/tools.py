"""Agent tools.

Tools are async and non-blocking so they never stall the event loop, and they
degrade to a readable message rather than raising, unless a failure mode has
been deliberately injected.
"""

from __future__ import annotations

import httpx

from common.config import Settings
from common.telemetry import get_logger

logger = get_logger(__name__)

DUCKDUCKGO_URL = "https://api.duckduckgo.com/"


class ToolError(RuntimeError):
    """Raised when a tool fails in a way the caller should surface."""


async def web_search(query: str, settings: Settings) -> str:
    """Query the DuckDuckGo Instant Answer API for lightweight public context."""
    if not settings.search_enabled:
        return "Search is disabled by configuration (SEARCH_ENABLED=false)."

    params = {"q": query, "format": "json", "no_html": "1", "no_redirect": "1"}
    try:
        async with httpx.AsyncClient(timeout=settings.search_timeout_seconds) as client:
            response = await client.get(DUCKDUCKGO_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001 - a flaky tool must not kill the run
        logger.warning("tool.web_search_failed", extra={"error": str(exc)[:200]})
        return f"Search unavailable: {type(exc).__name__}. Continue using existing knowledge."

    parts: list[str] = []
    if data.get("AbstractText"):
        source = data.get("AbstractURL") or "unknown source"
        parts.append(f"{data['AbstractText']} (source: {source})")

    for item in data.get("RelatedTopics", [])[:5]:
        if isinstance(item, dict) and item.get("Text"):
            url = item.get("FirstURL", "")
            parts.append(f"{item['Text']}{f' (source: {url})' if url else ''}")

    return "\n".join(parts) or "No concise result returned for this query."


def make_web_search(settings: Settings):
    """Bind settings into the pipeline's ``async (str) -> str`` tool signature."""

    async def _tool(query: str) -> str:
        return await web_search(query, settings)

    return _tool
