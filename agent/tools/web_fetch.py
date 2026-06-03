"""Phase 4 (support) — fetch competitor websites for specials/promos.

Free, no API needed. We fetch the page and return a trimmed plain-text version;
the synthesis step can scan it for specials the menu photos missed. Best-effort:
many Google results have no website, and some block scrapers — both yield None.
"""

from __future__ import annotations

import re

import httpx

from agent import config

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_CHARS = 4000  # cap text so it doesn't bloat the synthesis prompt

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RestaurantIntelAgent/0.1; +https://github.com)"
    )
}


def fetch_website_text(url: str) -> str | None:
    """Fetch a URL and return trimmed visible text, or None on failure.

    Strips script/style/markup and collapses whitespace. Truncated to
    _MAX_CHARS to keep the downstream prompt small.
    """
    if not url:
        return None
    try:
        resp = httpx.get(
            url, timeout=config.HTTP_TIMEOUT, follow_redirects=True, headers=_HEADERS
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    if "text/html" not in resp.headers.get("content-type", ""):
        return None

    text = _TAG_RE.sub(" ", resp.text)
    text = _HTML_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:_MAX_CHARS] or None
