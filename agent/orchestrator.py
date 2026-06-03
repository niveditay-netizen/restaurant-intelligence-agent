"""Phase 5 — the orchestrator: coordinates every tool into one report.

This is a code-orchestrated workflow (a deterministic Steps 1-8 pipeline), with
Claude called for the parts that need a model: vision menu extraction (Phase 2)
and the final synthesis. The synthesis system prompt is cached as a stable
prefix so repeat runs pay ~0.1x for that block.

Pass a `progress` callback to surface step-by-step status to a UI:
    run_agent("Piccolo Forno", "Pittsburgh", "slow Tuesdays",
              progress=lambda msg: print(msg))
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Callable

import anthropic

from agent import config, prompts
from agent.tools import events as events_tool
from agent.tools import google_places, vision, web_fetch, yelp

ProgressFn = Callable[[str], None]

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# How many top competitors get the (more expensive) Yelp + website enrichment.
_ENRICH_TOP_N = 3
_MENU_HIGHLIGHT_COUNT = 6


def _noop(_: str) -> None:
    pass


def _next_weekday_from_problem(problem: str, today: _dt.date | None = None) -> str | None:
    """If the problem mentions a weekday (e.g. 'slow Tuesdays'), return the next
    such date as ISO 'YYYY-MM-DD' to scope the events lookup. Else None."""
    if not problem:
        return None
    today = today or _dt.date.today()
    lowered = problem.lower()
    for name, idx in _WEEKDAYS.items():
        if name in lowered:
            delta = (idx - today.weekday()) % 7
            return (today + _dt.timedelta(days=delta)).isoformat()
    return None


def _parse_json(text: str) -> dict[str, Any]:
    """Parse Claude's JSON output, tolerating stray markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _menu_highlights(menu: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for dish in menu.get("dishes", [])[:_MENU_HIGHLIGHT_COUNT]:
        name = dish.get("name")
        price = dish.get("price")
        if name:
            names.append(f"{name} (${price:g})" if isinstance(price, (int, float)) else name)
    return names


def _synthesize(
    restaurant_profile: dict[str, Any],
    competitor_data: list[dict[str, Any]],
    events_data: list[dict[str, Any]],
    problem: str,
) -> dict[str, Any]:
    """Call Claude for the strategy report. Returns parsed synthesis JSON."""
    config.require_keys("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=config.MODEL,
        max_tokens=4096,
        thinking=config.THINKING,
        # System prompt is stable across runs → cache the prefix.
        system=[
            {
                "type": "text",
                "text": prompts.SYNTHESIS_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": prompts.build_synthesis_user_message(
                    restaurant_profile, competitor_data, events_data, problem
                ),
            }
        ],
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return _parse_json(text)
    except (json.JSONDecodeError, IndexError):
        # Never hard-fail on a parse miss — surface the raw text instead.
        return {
            "diagnosis": text or "Synthesis returned no parseable output.",
            "market_gaps": [],
            "recommendations": [],
            "what_not_to_do": [],
            "events_context": "",
        }


def run_agent(
    restaurant_name: str,
    city: str,
    problem: str = "general competitive analysis",
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run the full competitive-intelligence pipeline and return a report.

    Returns the structured report described in the README (restaurant,
    competitors, diagnosis, market_gaps, recommendations, what_not_to_do,
    events_context, data_sources). If the target can't be found, returns
    {"error": ...} so the UI can show a clean message.
    """
    say = progress or _noop
    data_sources: list[str] = []

    # --- Step 1: target restaurant profile ---------------------------------
    say("Finding your restaurant...")
    target = google_places.get_restaurant_profile(restaurant_name, city)
    if not target.get("found"):
        return {
            "error": f"Could not find '{restaurant_name}' in {city} via Google Places.",
        }
    data_sources.append("Google Places (restaurant profile)")

    # --- Step 2: nearby competitors ----------------------------------------
    say("Identifying nearby competitors...")
    cuisine = (target.get("cuisine_types") or [""])[0].replace("_restaurant", "")
    geo = target.get("geometry") or {}
    competitors_raw = google_places.get_nearby_competitors(
        place_id=target["place_id"],
        lat=geo.get("lat"),
        lng=geo.get("lng"),
        cuisine_type=cuisine,
    )
    data_sources.append("Google Places (nearby competitors)")

    # --- Step 3: read competitor menus from photos (vision) ----------------
    say("Reading competitor menus...")
    for comp in competitors_raw:
        refs = comp.get("photo_references") or []
        comp["menu"] = vision.extract_menu_from_photos(refs) if refs else {
            "dishes": [], "categories": [], "specials": [],
            "price_range": {"min": None, "max": None},
            "menus_found": 0, "photos_checked": 0,
        }
    if any(c["menu"].get("menus_found") for c in competitors_raw):
        data_sources.append("Claude vision (menu extraction)")

    # --- Step 4: Yelp data for target + top competitors (optional) ---------
    # Skipped gracefully if no Yelp key is configured.
    target_yelp: dict[str, Any] = {"found": False}
    if config.YELP_API_KEY:
        say("Fetching reviews...")
        target_yelp = yelp.get_yelp_business(restaurant_name, city)
        if target_yelp.get("found"):
            target_yelp["reviews"] = yelp.get_yelp_reviews(target_yelp["business_id"])
            data_sources.append("Yelp Fusion (reviews, max 3/business)")

        for comp in competitors_raw[:_ENRICH_TOP_N]:
            biz = yelp.get_yelp_business(comp.get("name") or "", city)
            if biz.get("found"):
                biz["reviews"] = yelp.get_yelp_reviews(biz["business_id"])
            comp["yelp"] = biz

    # --- Step 5: local events (optional) -----------------------------------
    # Skipped gracefully if no Ticketmaster key is configured.
    events_data: list[dict[str, Any]] = []
    if config.TICKETMASTER_API_KEY:
        say("Checking local events...")
        target_day = _next_weekday_from_problem(problem)
        events_data = events_tool.get_local_events(city, target_day)
        if events_data:
            data_sources.append("Ticketmaster Discovery (local events)")

    # --- Step 6: competitor websites for specials (best-effort) ------------
    for comp in competitors_raw[:_ENRICH_TOP_N]:
        site = (comp.get("yelp") or {}).get("url")
        text = web_fetch.fetch_website_text(site) if site else None
        if text:
            comp["website_text"] = text
    if any(c.get("website_text") for c in competitors_raw[:_ENRICH_TOP_N]):
        data_sources.append("Competitor websites (specials)")

    # --- Step 7: synthesis -------------------------------------------------
    say("Synthesizing recommendations...")
    # Build a compact view for the model (drop bulky raw fields we don't need).
    competitor_view = [
        {
            "name": c.get("name"),
            "rating": c.get("rating"),
            "price_level": c.get("price_level"),
            "user_ratings_total": c.get("user_ratings_total"),
            "cuisine_types": c.get("cuisine_types"),
            "menu": c.get("menu"),
            "yelp": {
                "rating": (c.get("yelp") or {}).get("rating"),
                "price": (c.get("yelp") or {}).get("price"),
                "categories": (c.get("yelp") or {}).get("categories"),
                "reviews": (c.get("yelp") or {}).get("reviews"),
            },
            "website_excerpt": (c.get("website_text") or "")[:1500] or None,
        }
        for c in competitors_raw
    ]
    restaurant_view = {**target, "yelp": target_yelp}
    synthesis = _synthesize(restaurant_view, competitor_view, events_data, problem)

    # --- Step 8: assemble the report ---------------------------------------
    competitor_cards = [
        {
            "name": c.get("name"),
            "rating": c.get("rating"),
            "menu_highlights": _menu_highlights(c.get("menu") or {}),
            "review_themes": [
                (r.get("text") or "")[:160]
                for r in ((c.get("yelp") or {}).get("reviews") or [])
            ],
        }
        for c in competitors_raw
    ]

    return {
        "restaurant": {
            "name": target.get("name"),
            "location": target.get("address"),
            "rating": target.get("rating"),
            "cuisine": target.get("cuisine_types"),
            "price_level": target.get("price_level"),
        },
        "competitors": competitor_cards,
        "diagnosis": synthesis.get("diagnosis", ""),
        "market_gaps": synthesis.get("market_gaps", []),
        "recommendations": synthesis.get("recommendations", []),
        "what_not_to_do": synthesis.get("what_not_to_do", []),
        "events_context": synthesis.get("events_context", ""),
        "data_sources": data_sources,
        # Authoritative competitor names straight from Google Places — the eval's
        # hallucination check verifies output names are a subset of this.
        "_google_competitor_names": [c.get("name") for c in competitors_raw],
    }
