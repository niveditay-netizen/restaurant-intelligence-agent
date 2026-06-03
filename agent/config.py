"""Central configuration: model IDs, API keys, and tunable knobs.

Keys are read from environment variables (loaded from .env locally via
python-dotenv, or from the Streamlit secrets manager in production).
"""

from __future__ import annotations

import os

# python-dotenv is optional at runtime (Streamlit Cloud injects env directly).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


# --- Models -----------------------------------------------------------------
# One constant so swapping models is a one-line change. Sonnet 4.6 has strong
# vision + tool use at a lower price than Opus, which fits this multi-call agent.
MODEL = "claude-sonnet-4-6"

# Adaptive thinking lets Claude decide how much to reason per call (no fixed
# budget_tokens on 4.6). Used for the synthesis + vision reasoning calls.
THINKING = {"type": "adaptive"}


# --- API keys ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
YELP_API_KEY = os.environ.get("YELP_API_KEY", "")
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY", "")

# Required to run the app (Google for data, Anthropic for vision + synthesis).
# Yelp + Ticketmaster are optional — the agent skips them if their key is unset.
REQUIRED_KEYS = ("ANTHROPIC_API_KEY", "GOOGLE_PLACES_API_KEY")

# Optional gate for a public demo: if set, the UI requires this password before
# anyone can run an (API-cost-incurring) analysis. Unset = open (local dev).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


# --- Tunable knobs ----------------------------------------------------------
MAX_COMPETITORS = 5            # competitors pulled from Nearby Search
MAX_PHOTOS_PER_COMPETITOR = 5  # photo references fetched per competitor
COMPETITOR_RADIUS_METERS = 800
HTTP_TIMEOUT = 20.0            # seconds, per outbound API request


def require_keys(*names: str) -> None:
    """Raise a clear error if any required key is missing.

    Call at the start of an operation that needs specific keys so failures
    surface as actionable messages instead of opaque 401s deep in a request.
    """
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise RuntimeError(
            "Missing required API key(s): "
            + ", ".join(missing)
            + ". Set them in your .env (see .env.example) or Streamlit secrets."
        )
