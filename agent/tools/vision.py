"""Phase 2 — Vision: read competitor menus from Google Places photos.

For each photo reference we: fetch the image, base64-encode it, and ask Claude
(vision) whether it's a menu and, if so, to extract structured menu data. Photos
that aren't menus (dishes, storefronts) are skipped cheaply via the same call.

Quick manual test (needs ANTHROPIC_API_KEY + GOOGLE_PLACES_API_KEY and a real
photo_reference from google_places.get_nearby_competitors):
    python -c "from agent.tools.vision import extract_menu_from_photos as e; \
import json; print(json.dumps(e(['<photo_ref>']), indent=2))"
"""

from __future__ import annotations

import base64
import json
from typing import Any

import anthropic
import httpx

from agent import config, prompts
from agent.tools.google_places import photo_url

# Media types Claude vision accepts. Google photos are typically JPEG.
_ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        config.require_keys("ANTHROPIC_API_KEY")
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _fetch_image(photo_reference: str) -> tuple[str, str] | None:
    """Fetch a Places photo and return (base64_data, media_type), or None.

    The Place Photo endpoint 302-redirects to the actual image, so we must
    follow redirects. Returns None if the image is missing or an unsupported type.
    """
    try:
        resp = httpx.get(
            photo_url(photo_reference),
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in _ALLOWED_MEDIA:
        return None
    return base64.standard_b64encode(resp.content).decode("utf-8"), media_type


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort JSON parse; tolerate accidental markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _extract_one(photo_reference: str) -> dict[str, Any] | None:
    """Run the is-menu check + extraction on a single photo. None if not a menu."""
    fetched = _fetch_image(photo_reference)
    if fetched is None:
        return None
    data_b64, media_type = fetched

    message = _anthropic().messages.create(
        model=config.MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data_b64,
                        },
                    },
                    {"type": "text", "text": prompts.MENU_EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        parsed = _parse_json(text)
    except (json.JSONDecodeError, IndexError):
        return None

    if not parsed.get("is_menu"):
        return None
    return parsed


def extract_menu_from_photos(photo_references: list[str]) -> dict[str, Any]:
    """Read menus from a list of photo references and merge into one structure.

    Returns:
        {
          "dishes": [{"name", "description", "price"}],
          "categories": [str],
          "price_range": {"min": float|None, "max": float|None},
          "specials": [str],
          "menus_found": int,        # how many photos were actually menus
          "photos_checked": int,
        }
    """
    dishes: list[dict[str, Any]] = []
    categories: set[str] = set()
    specials: set[str] = set()
    prices: list[float] = []
    menus_found = 0

    for ref in photo_references:
        result = _extract_one(ref)
        if result is None:
            continue
        menus_found += 1
        dishes.extend(result.get("dishes") or [])
        categories.update(result.get("categories") or [])
        specials.update(result.get("specials") or [])
        for d in result.get("dishes") or []:
            if isinstance(d.get("price"), (int, float)):
                prices.append(float(d["price"]))

    return {
        "dishes": dishes,
        "categories": sorted(categories),
        "price_range": {
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
        },
        "specials": sorted(specials),
        "menus_found": menus_found,
        "photos_checked": len(photo_references),
    }
