"""Restaurant classifier — determines if a business is a restaurant or not.

Uses Google Places types array as the primary signal and name keyword matching
as a fast fallback that requires no API call. Called by clean_cohort.py and
cohort_builder.py before onboarding any business into the backtest cohort.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GOOGLE_PLACE_DETAIL_URL = "https://places.googleapis.com/v1/places/{}"

# Google Place types that confirm a business is a restaurant/food service venue.
RESTAURANT_TYPES = frozenset({
    "restaurant", "food", "meal_takeaway", "meal_delivery",
    "bar", "cafe", "bakery", "night_club",
})

# Google Place types that confirm a business is NOT a restaurant.
NON_RESTAURANT_TYPES = frozenset({
    "liquor_store", "convenience_store", "grocery_or_supermarket",
    "food_store", "gas_station", "supermarket",
})

# Name substrings (lowercase) that strongly indicate a non-restaurant.
# Matched with a simple `in` check so "liquor" catches "AAAA Liquor", etc.
NON_RESTAURANT_KEYWORDS = frozenset({
    # Alcohol/tobacco retail
    "liquor", "spirits", "wine & beer", "wine and beer",
    "off premise", "package store", "tobacco", "beverage depot",
    # Convenience / gas stations
    "convenience", "c-store", "express mart", "food mart",
    "mini mart", "minimart", "quick stop", "quick sak", "quik sak",
    "corner store", "food & fuel",
    "7-eleven", "7eleven",
    "murphy express", "murphy usa", "raceway",
    "chevron", "sunoco", "texaco", "speedmax",
    # Grocery / markets
    "aldi", "grocery", "meat market", "mercado",
    "food store",
    # Lodging
    "hotel", "suites",
    # Kwik-* convenience chains
    "kwik",
})


def classify(name: str, google_place_id: Optional[str] = None) -> dict:
    """Classify a business as RESTAURANT, NON_RESTAURANT, or UNKNOWN.

    Checks name keywords first (no API cost), then fetches Google Place types
    when a place ID is available.

    Returns:
        {
            "classification": "RESTAURANT" | "NON_RESTAURANT" | "UNKNOWN",
            "confidence":     "high" | "medium" | "low",
            "reason":         str,
            "types":          list[str],   # Google Place types if fetched, else []
        }
    """
    name_lower = name.lower()

    # ── 1. Fast name keyword check — no API call needed ───────────────────────
    for kw in NON_RESTAURANT_KEYWORDS:
        if kw in name_lower:
            return {
                "classification": "NON_RESTAURANT",
                "confidence":     "high",
                "reason":         f"name contains '{kw}'",
                "types":          [],
            }

    # ── 2. Google Place types check (most reliable) ───────────────────────────
    if google_place_id:
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if api_key:
            types = _fetch_place_types(google_place_id, api_key)
            if types:
                matched_restaurant = [t for t in types if t in RESTAURANT_TYPES]
                matched_non = [t for t in types if t in NON_RESTAURANT_TYPES]

                if matched_restaurant:
                    return {
                        "classification": "RESTAURANT",
                        "confidence":     "high",
                        "reason":         f"Google types include {matched_restaurant}",
                        "types":          types,
                    }
                if matched_non:
                    return {
                        "classification": "NON_RESTAURANT",
                        "confidence":     "high",
                        "reason":         f"Google types include {matched_non}",
                        "types":          types,
                    }
                # Types fetched but not in either known set — lean toward keeping.
                return {
                    "classification": "UNKNOWN",
                    "confidence":     "low",
                    "reason":         f"Google types {types} unrecognised",
                    "types":          types,
                }

    # ── 3. No API key or no place ID — benefit of the doubt ───────────────────
    return {
        "classification": "UNKNOWN",
        "confidence":     "low",
        "reason":         "no Google Place ID or API key — not classified",
        "types":          [],
    }


def is_restaurant(name: str, google_place_id: Optional[str] = None) -> bool:
    """Return True unless the business is confidently classified as NON_RESTAURANT."""
    return classify(name, google_place_id)["classification"] != "NON_RESTAURANT"


def _fetch_place_types(place_id: str, api_key: str) -> list[str]:
    """Fetch the types array for a Google Place."""
    try:
        resp = httpx.get(
            _GOOGLE_PLACE_DETAIL_URL.format(place_id),
            headers={
                "X-Goog-Api-Key":    api_key,
                "X-Goog-FieldMask":  "types",
            },
            timeout=15.0,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json().get("types", [])
    except Exception as exc:
        logger.warning("[classifier] Could not fetch types for %s: %s", place_id, exc)
        return []
