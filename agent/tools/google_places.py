"""Phase 1 — Google Places: find the target restaurant and its competitors.

Uses the (legacy) Places API JSON endpoints, which the $200/month free credit
covers comfortably for demo use:
  - Text Search   $0.017/req  https://maps.googleapis.com/maps/api/place/textsearch/json
  - Nearby Search $0.032/req  https://maps.googleapis.com/maps/api/place/nearbysearch/json
  - Place Details             https://maps.googleapis.com/maps/api/place/details/json
  - Place Photos              https://maps.googleapis.com/maps/api/place/photo
A single agent run costs roughly $0.10-0.15 in Google credits.

Quick manual test:
    python -c "from agent.tools.google_places import get_restaurant_profile; \
import json; print(json.dumps(get_restaurant_profile('Piccolo Forno', 'Pittsburgh'), indent=2))"
"""

from __future__ import annotations

from typing import Any

import httpx

from agent import config

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"

# Fields requested from Place Details. Keep tight to control cost.
_DETAIL_FIELDS = (
    "place_id,name,formatted_address,rating,price_level,types,"
    "opening_hours,user_ratings_total,geometry,photos"
)

# Google `types` we treat as cuisine signals when matching competitors.
_NON_CUISINE_TYPES = {
    "restaurant",
    "food",
    "point_of_interest",
    "establishment",
    "meal_takeaway",
    "meal_delivery",
    "store",
}


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET a Places JSON endpoint and raise on a non-OK API status."""
    params = {**params, "key": config.GOOGLE_PLACES_API_KEY}
    resp = httpx.get(url, params=params, timeout=config.HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    # ZERO_RESULTS is a valid "found nothing" outcome, not an error.
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(
            f"Google Places error: {status} - {data.get('error_message', 'no detail')}"
        )
    return data


def _cuisine_types(types: list[str] | None) -> list[str]:
    """Strip generic types, leaving cuisine-ish ones (e.g. 'italian_restaurant')."""
    return [t for t in (types or []) if t not in _NON_CUISINE_TYPES]


def _photo_refs(photos: list[dict[str, Any]] | None, limit: int) -> list[str]:
    return [p["photo_reference"] for p in (photos or []) if "photo_reference" in p][:limit]


def _place_details(place_id: str) -> dict[str, Any]:
    data = _get(DETAILS_URL, {"place_id": place_id, "fields": _DETAIL_FIELDS})
    return data.get("result", {}) or {}


def _shape_profile(result: dict[str, Any], include_photos: bool = False) -> dict[str, Any]:
    """Normalize a Places result into our internal profile dict."""
    geometry = (result.get("geometry") or {}).get("location") or {}
    profile: dict[str, Any] = {
        "place_id": result.get("place_id"),
        "name": result.get("name"),
        "address": result.get("formatted_address") or result.get("vicinity"),
        "rating": result.get("rating"),
        "price_level": result.get("price_level"),
        "types": result.get("types", []),
        "cuisine_types": _cuisine_types(result.get("types")),
        "opening_hours": (result.get("opening_hours") or {}).get("weekday_text"),
        "user_ratings_total": result.get("user_ratings_total"),
        "geometry": {"lat": geometry.get("lat"), "lng": geometry.get("lng")},
    }
    if include_photos:
        profile["photo_references"] = _photo_refs(
            result.get("photos"), config.MAX_PHOTOS_PER_COMPETITOR
        )
    return profile


def get_restaurant_profile(name: str, city: str) -> dict[str, Any]:
    """Find a restaurant by name + city and return its normalized profile.

    Returns a dict with keys: place_id, name, address, rating, price_level,
    types, cuisine_types, opening_hours, user_ratings_total, geometry. If no
    match is found, returns {"found": False, "query": ...}.
    """
    config.require_keys("GOOGLE_PLACES_API_KEY")

    data = _get(TEXT_SEARCH_URL, {"query": f"{name} {city}", "type": "restaurant"})
    results = data.get("results", [])
    if not results:
        return {"found": False, "query": f"{name} {city}"}

    # Text Search returns a usable result, but Details has richer fields
    # (opening hours, full photo list), so hydrate from the top hit.
    top = results[0]
    place_id = top.get("place_id")
    detailed = _place_details(place_id) if place_id else top
    profile = _shape_profile(detailed or top)
    profile["found"] = True
    return profile


def get_nearby_competitors(
    place_id: str,
    lat: float,
    lng: float,
    cuisine_type: str,
    radius_meters: int = config.COMPETITOR_RADIUS_METERS,
) -> list[dict[str, Any]]:
    """Find nearby competitors of the same cuisine, ranked by review volume.

    Filters out the target itself, prefers same-cuisine matches, and hydrates
    each competitor with Place Details so we get photo references (up to
    MAX_PHOTOS_PER_COMPETITOR each) for Phase 2 vision processing.
    """
    config.require_keys("GOOGLE_PLACES_API_KEY")

    data = _get(
        NEARBY_SEARCH_URL,
        {
            "location": f"{lat},{lng}",
            "radius": radius_meters,
            "type": "restaurant",
            "keyword": cuisine_type or "restaurant",
        },
    )
    candidates = [r for r in data.get("results", []) if r.get("place_id") != place_id]

    # Prefer places that share a cuisine type with the target; fall back to all.
    target_cuisine = (cuisine_type or "").lower()
    same_cuisine = [
        r
        for r in candidates
        if any(target_cuisine in t.lower() for t in r.get("types", []))
    ] if target_cuisine else []
    pool = same_cuisine or candidates

    # Rank by how many reviews — a rough proxy for being an established rival.
    pool.sort(key=lambda r: r.get("user_ratings_total") or 0, reverse=True)

    competitors: list[dict[str, Any]] = []
    for r in pool[: config.MAX_COMPETITORS]:
        cid = r.get("place_id")
        detailed = _place_details(cid) if cid else r
        competitors.append(_shape_profile(detailed or r, include_photos=True))
    return competitors


def photo_url(photo_reference: str, max_width: int = 800) -> str:
    """Build a Place Photo URL for a reference (used by the vision tool)."""
    return (
        f"{PHOTO_URL}?maxwidth={max_width}"
        f"&photo_reference={photo_reference}&key={config.GOOGLE_PLACES_API_KEY}"
    )
