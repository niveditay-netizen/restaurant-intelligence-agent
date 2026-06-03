"""Phase 3 — Yelp Fusion: business data + a small sample of review text.

NOTE on limits: the Yelp Fusion free tier returns at most 3 reviews per
business, and only review excerpts (not full text). That's enough to surface
themes, not to do exhaustive sentiment analysis — this limitation is called out
in the README and the eval notes.

Base URL: https://api.yelp.com/v3/

Quick manual test (needs YELP_API_KEY):
    python -c "from agent.tools.yelp import get_yelp_business; \
import json; print(json.dumps(get_yelp_business('Piccolo Forno', 'Pittsburgh'), indent=2))"
"""

from __future__ import annotations

from typing import Any

import httpx

from agent import config

BASE_URL = "https://api.yelp.com/v3"
_FREE_TIER_REVIEW_CAP = 3


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.YELP_API_KEY}"}


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.get(
        f"{BASE_URL}{path}",
        params=params,
        headers=_headers(),
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_yelp_business(name: str, city: str) -> dict[str, Any]:
    """Look up a business via Yelp Business Search.

    Returns: {found, business_id, name, rating, review_count, price,
    categories, url}. If nothing matches, returns {"found": False}.
    """
    config.require_keys("YELP_API_KEY")

    data = _get(
        "/businesses/search",
        {"term": name, "location": city, "limit": 1, "categories": "restaurants"},
    )
    businesses = data.get("businesses", [])
    if not businesses:
        return {"found": False, "query": f"{name} {city}"}

    b = businesses[0]
    return {
        "found": True,
        "business_id": b.get("id"),
        "name": b.get("name"),
        "rating": b.get("rating"),
        "review_count": b.get("review_count"),
        "price": b.get("price"),
        "categories": [c.get("title") for c in b.get("categories", [])],
        "url": b.get("url"),
    }


def get_yelp_reviews(business_id: str, limit: int = _FREE_TIER_REVIEW_CAP) -> list[dict[str, Any]]:
    """Fetch up to 3 review excerpts (free-tier cap) for a business.

    Returns a list of {text, rating, time_created}. Returns [] on any error so
    a missing-review case never breaks the orchestration.
    """
    config.require_keys("YELP_API_KEY")
    limit = min(limit, _FREE_TIER_REVIEW_CAP)

    try:
        data = _get(f"/businesses/{business_id}/reviews", {"limit": limit})
    except httpx.HTTPError:
        return []

    return [
        {
            "text": r.get("text"),
            "rating": r.get("rating"),
            "time_created": r.get("time_created"),
        }
        for r in data.get("reviews", [])[:limit]
    ]
