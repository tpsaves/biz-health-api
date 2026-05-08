import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SEARCH_URL  = "https://places-api.foursquare.com/places/search"
_DETAILS_URL = "https://places-api.foursquare.com/places/{fsq_id}"

# Foursquare retired the v3 api.foursquare.com endpoints (410 Gone as of May 2026).
# The new Places API lives at places-api.foursquare.com and requires Bearer auth
# plus an X-Places-Api-Version header. Auth is Bearer even for service API keys.
_API_VERSION = "2025-06-17"

# Details are a paid/premium call on the free tier. The search call (which returns
# venue ID, location, and category) is free. We attempt details and degrade
# gracefully on 429 so the scraper works on the free tier.
_DETAIL_FIELDS = "name,rating,stats,location"


def scrape_venue(name: str, lat: float, lng: float, restaurant_id: str, session: Session) -> dict:
    """Search Foursquare for a venue by name and coordinates, fetch details if available,
    and write to raw_signals.

    Uses the new Places API (places-api.foursquare.com) which replaced the v3 endpoints
    retired in May 2026. Auth is Bearer with a service API key (not the old bare-key style).
    The details call is premium-only; on the free tier it returns 429 and is skipped.
    """
    api_key = os.environ["FOURSQUARE_API_KEY"]
    # Bearer prefix is required on the new Places API even for service API keys
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Places-Api-Version": _API_VERSION,
        "Accept": "application/json",
    }

    # Search is a free call; the server can take ~15s on the free tier, so use 30s timeout
    search_resp = httpx.get(
        _SEARCH_URL,
        params={"query": name, "ll": f"{lat},{lng}", "limit": 1},
        headers=headers,
        timeout=30.0,
    )
    search_resp.raise_for_status()
    search_data = search_resp.json()

    results = search_data.get("results", [])
    if not results:
        raise RuntimeError(f"No Foursquare venue found for {name!r} at ll={lat},{lng}")

    # New API uses fsq_place_id; old v3 used fsq_id — check both for safety
    first = results[0]
    fsq_id = first.get("fsq_place_id") or first.get("fsq_id")
    if not fsq_id:
        raise RuntimeError(f"Search result for {name!r} missing place ID: {first}")

    # Attempt the details call (premium). On 429 (out of credits) we store an empty
    # dict so the row still lands in raw_signals and the scoring engine degrades to
    # Google-only rating rather than crashing.
    details: dict = {}
    try:
        details_resp = httpx.get(
            _DETAILS_URL.format(fsq_id=fsq_id),
            params={"fields": _DETAIL_FIELDS},
            headers=headers,
            timeout=30.0,
        )
        if details_resp.status_code == 429:
            # 429 means no API credits remaining — details are a premium call
            logger.warning("Foursquare details skipped (no credits) — fsq_id=%s", fsq_id)
        else:
            details_resp.raise_for_status()
            details = details_resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("Foursquare details failed (%s) — fsq_id=%s", exc.response.status_code, fsq_id)

    payload = {"search": search_data, "details": details}

    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source": "foursquare",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    logger.info(
        "Stored Foursquare signal — fsq_id=%s name=%s rating=%s",
        fsq_id,
        details.get("name") or first.get("name"),
        details.get("rating", "(no credits)"),
    )
    return payload
