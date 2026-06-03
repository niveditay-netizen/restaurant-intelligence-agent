"""Phase 4 — Ticketmaster Discovery: local ticketed events near the restaurant.

Used to explain foot-traffic patterns ("a Pirates game Tuesday explains why the
Strip District is busy but Squirrel Hill is slow"). Covers ticketed events only
— farmers markets, street festivals, etc. won't appear (noted in the README).

Endpoint: https://app.ticketmaster.com/discovery/v2/events.json
Free tier: 5000 calls/day, no credit card.

Quick manual test (needs TICKETMASTER_API_KEY):
    python -c "from agent.tools.events import get_local_events; \
import json; print(json.dumps(get_local_events('Pittsburgh', '2026-06-09'), indent=2))"
"""

from __future__ import annotations

from typing import Any

import httpx

from agent import config

EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def get_local_events(
    city: str,
    target_day: str | None = None,
    radius_km: int = 5,
) -> list[dict[str, Any]]:
    """Find events near a city, optionally constrained to a single day.

    Args:
        city: City name (Ticketmaster geocodes it).
        target_day: ISO date "YYYY-MM-DD". If given, restricts to that day.
        radius_km: Search radius in kilometers.

    Returns a list of {name, venue, date, category, attendance_estimate}.
    Returns [] on any error so events are always optional context.
    """
    config.require_keys("TICKETMASTER_API_KEY")

    params: dict[str, Any] = {
        "apikey": config.TICKETMASTER_API_KEY,
        "city": city,
        "radius": radius_km,
        "unit": "km",
        "sort": "date,asc",
        "size": 20,
    }
    if target_day:
        params["startDateTime"] = f"{target_day}T00:00:00Z"
        params["endDateTime"] = f"{target_day}T23:59:59Z"

    try:
        resp = httpx.get(EVENTS_URL, params=params, timeout=config.HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError:
        return []

    events = (data.get("_embedded") or {}).get("events", [])
    out: list[dict[str, Any]] = []
    for e in events:
        venues = (e.get("_embedded") or {}).get("venues", [])
        venue = venues[0] if venues else {}
        classifications = e.get("classifications", [])
        category = None
        if classifications:
            category = (classifications[0].get("segment") or {}).get("name")
        out.append(
            {
                "name": e.get("name"),
                "venue": venue.get("name"),
                "date": (e.get("dates") or {}).get("start", {}).get("localDate"),
                "category": category,
                # Ticketmaster doesn't expose attendance; capacity is a rough proxy.
                "attendance_estimate": venue.get("capacity"),
            }
        )
    return out
