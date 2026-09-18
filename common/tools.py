from crewai.tools import tool
import json
import urllib.parse
import urllib.request


@tool("web_search")
def web_search(query: str) -> str:
    """Search DuckDuckGo Instant Answer API for lightweight public-web context."""
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        parts = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                parts.append(item["Text"])
        return "\n".join(parts) or "No concise result returned."
    except Exception as exc:
        return f"Search unavailable: {exc}"
