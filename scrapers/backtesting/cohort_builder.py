"""Backtesting Part 2 — Backtest Cohort Builder.

Builds a prospective cohort of 200–500 DFW restaurants distributed across
the four risk bands, scored with the existing engine, and written to the
backtest_cohort table for 90/180-day outcome tracking.

Risk band targets:
  Low (80-100):      ~150 restaurants
  Moderate (60-79):  ~150 restaurants
  Elevated (40-59):  ~100 restaurants
  High (0-39):       ~50 restaurants

Targeted searches for lower-scoring restaurants:
  - Dallas OpenData: inspection scores below 70 in last 6 months
  - TABC: suspended or recently reinstated licences
  - Google Places: ratings below 3.5 with fewer than 50 reviews
"""

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from scoring.engine_v2 import compute_scores_v2
from signals.google_places import scrape_place
from signals.health_inspections import scrape_inspections
from signals.tabc_license import scrape_license
from signals.hours_monitor import scrape_hours
from backtesting.restaurant_classifier import NON_RESTAURANT_KEYWORDS, NON_RESTAURANT_TYPES

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_DFW_CITIES = [
    "Dallas", "Fort Worth", "Arlington", "Grand Prairie",
    "Plano", "Frisco", "McKinney", "Irving", "Garland", "Denton",
]

_CITY_CENTERS: dict[str, dict] = {
    "Dallas":        {"latitude": 32.7767, "longitude": -96.7970},
    "Fort Worth":    {"latitude": 32.7555, "longitude": -97.3308},
    "Arlington":     {"latitude": 32.7357, "longitude": -97.1081},
    "Grand Prairie": {"latitude": 32.7459, "longitude": -97.0164},
    "Plano":         {"latitude": 33.0198, "longitude": -96.6989},
    "Frisco":        {"latitude": 33.1507, "longitude": -96.8236},
    "McKinney":      {"latitude": 33.1972, "longitude": -96.6397},
    "Irving":        {"latitude": 32.8140, "longitude": -96.9489},
    "Garland":       {"latitude": 32.9126, "longitude": -96.6389},
    "Denton":        {"latitude": 33.2148, "longitude": -97.1331},
}

_GOOGLE_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_TABC_URL = "https://data.texas.gov/resource/kguh-7q9z.json"
_DALLAS_OD_INSP_URL = "https://www.dallasopendata.com/resource/dri5-wcct.json"


# ── Risk band helpers ──────────────────────────────────────────────────────────

def risk_band(score: int) -> str:
    if score >= 80:
        return "low"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "elevated"
    return "high"


_RISK_BAND_TARGETS = {"low": 150, "moderate": 150, "elevated": 100, "high": 50}


# ── Restaurant discovery ───────────────────────────────────────────────────────

def _find_google_restaurants(city: str, api_key: str, min_rating: float = 0.0, max_reviews: Optional[int] = None) -> list[dict]:
    """Search Google Places for active restaurants in a DFW city.

    Optional filters allow targeting lower-scoring cohort members:
      min_rating: include only places with rating <= this value (for low-rated search)
      max_reviews: include only places with userRatingCount <= this value
    """
    if not api_key:
        return []

    results = []
    page_token = None
    fetched = 0

    try:
        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.rating,places.userRatingCount,places.businessStatus,"
                "places.addressComponents,places.types"
            ),
        }

        body = {
            "textQuery": f"restaurant in {city} Texas",
            "includedType": "restaurant",
            "locationBias": {
                "circle": {
                    "center": _CITY_CENTERS[city],
                    "radius": 12000.0,
                }
            },
            "maxResultCount": 20,
        }

        resp = httpx.post(_GOOGLE_SEARCH_URL, headers=headers, json=body, timeout=30.0)
        resp.raise_for_status()

        for place in resp.json().get("places", []):
            if place.get("businessStatus") != "OPERATIONAL":
                continue

            # Skip anything Google classifies as a non-restaurant type.
            place_types = place.get("types", [])
            if any(t in NON_RESTAURANT_TYPES for t in place_types):
                continue

            rating = place.get("rating")
            review_count = place.get("userRatingCount", 0)

            # Apply low-score targeting filters when specified.
            if min_rating and rating and rating > min_rating:
                continue
            if max_reviews and review_count > max_reviews:
                continue

            place_id = place.get("id", "")
            name = place.get("displayName", {}).get("text", "")
            formatted_addr = place.get("formattedAddress", "")

            # Parse street address (drop trailing city/state/zip).
            parts = [p.strip() for p in formatted_addr.split(",")]
            street = ", ".join(parts[:-2]) if len(parts) > 2 else formatted_addr

            zip_code = None
            for comp in place.get("addressComponents", []):
                if "postal_code" in comp.get("types", []):
                    zip_code = comp.get("longText") or comp.get("shortText")
                    break

            results.append({
                "google_place_id": place_id,
                "name": name,
                "address": street,
                "city": city,
                "zip": zip_code,
                "rating": rating,
                "review_count": review_count,
            })

    except Exception as exc:
        logger.error("[cohort] Google Places search failed for %s: %s", city, exc)

    return results


def _find_low_inspection_restaurants(city: str = "Dallas") -> list[dict]:
    """Find Dallas restaurants with inspection scores below 70 in the last 6 months.

    These are higher-risk candidates for the elevated/high risk cohort bands.
    Only Dallas OpenData is queried here; other cities use MyHealthDepartment
    which doesn't expose a public filtering API.
    """
    if city.lower() != "dallas":
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat()
    results = []

    try:
        # Dallas OpenData covers Oct 2016 – Jan 2024; fields: program_identifier, insp_date.
        resp = httpx.get(
            _DALLAS_OD_INSP_URL,
            params={
                "$where": f"score < 70 AND insp_date > '{cutoff}'",
                "$select": "program_identifier,site_address,zip",
                "$order": "score ASC",
                "$limit": 200,
            },
            timeout=60.0,
        )
        resp.raise_for_status()

        for rec in resp.json():
            name = (rec.get("program_identifier") or "").strip()
            if not name:
                continue
            # Exclude obvious non-restaurant businesses by name before attempting
            # Google Place ID resolution (avoids wasted API calls).
            name_lower = name.lower()
            if any(kw in name_lower for kw in NON_RESTAURANT_KEYWORDS):
                continue
            results.append({
                "name": name,
                "address": rec.get("site_address", ""),
                "city": "Dallas",
                "zip": rec.get("zip"),
                "google_place_id": None,
            })

    except Exception as exc:
        logger.error("[cohort] Dallas inspection query failed: %s", exc)

    return results


def _find_tabc_at_risk(cities: list[str]) -> list[dict]:
    """Find TABC licences with low-tier licence types in DFW as a risk proxy.

    The TABC dataset (kguh-7q9z) contains only ACTIVE licences with no status
    column.  Suspended/reinstated filtering is not possible via this endpoint.
    As a substitute we return restaurants with off-premise or catering-only
    permits (BQ/P), which represent a downgrade in operational scope and
    correlate with financial stress.
    """
    city_filter = " OR ".join(f"upper(city)='{c.upper()}'" for c in cities)
    results = []

    try:
        resp = httpx.get(
            _TABC_URL,
            params={
                "$where": (
                    f"(aimslicensetype='BQ' OR aimslicensetype='P') "
                    f"AND ({city_filter})"
                ),
                "$select": "aimsownername,aimstradename,city,locationaddress",
                "$limit": 200,
            },
            timeout=60.0,
        )
        resp.raise_for_status()

        for rec in resp.json():
            name = (rec.get("aimstradename") or rec.get("aimsownername", "")).strip()
            if not name:
                continue
            # BQ (beer/wine off-premise) and P (caterer) licences include many
            # liquor stores and C-stores — skip them by name before API resolution.
            name_lower = name.lower()
            if any(kw in name_lower for kw in NON_RESTAURANT_KEYWORDS):
                continue
            results.append({
                "name": name,
                "address": rec.get("locationaddress", ""),
                "city": rec.get("city", "").title(),
                "zip": None,
                "google_place_id": None,
            })

    except Exception as exc:
        logger.error("[cohort] TABC at-risk query failed: %s", exc)

    return results


# ── Onboarding helper ─────────────────────────────────────────────────────────

def _upsert_restaurant(restaurant: dict, session: Session) -> str:
    """Insert restaurant if not present, return its UUID.

    Matches on google_place_id first, then name+city fuzzy (exact here for simplicity).
    Returns the restaurant's id as a string.
    """
    place_id = restaurant.get("google_place_id")

    if place_id:
        row = session.execute(
            text("SELECT id FROM restaurants WHERE google_place_id = :pid"),
            {"pid": place_id},
        ).one_or_none()
        if row:
            return str(row.id)

    # Fall back to name+city match.
    row = session.execute(
        text(
            "SELECT id FROM restaurants "
            "WHERE lower(name) = lower(:name) AND lower(city) = lower(:city) LIMIT 1"
        ),
        {"name": restaurant["name"], "city": restaurant.get("city", "")},
    ).one_or_none()
    if row:
        # Backfill google_place_id if we now have it.
        if place_id:
            session.execute(
                text("UPDATE restaurants SET google_place_id = :pid WHERE id = :id"),
                {"pid": place_id, "id": str(row.id)},
            )
            session.commit()
        return str(row.id)

    # Insert new record.
    new_id = str(uuid.uuid4())
    session.execute(
        text(
            """
            INSERT INTO restaurants (id, name, address, city, state, zip, google_place_id)
            VALUES (:id, :name, :address, :city, 'TX', :zip, :google_place_id)
            """
        ),
        {
            "id":             new_id,
            "name":           restaurant["name"],
            "address":        restaurant.get("address", ""),
            "city":           restaurant.get("city", ""),
            "zip":            restaurant.get("zip"),
            "google_place_id": place_id,
        },
    )
    session.commit()
    return new_id


def _scrape_and_score(restaurant_id: str, google_place_id: str, name: str, city: str, session: Session) -> Optional[dict]:
    """Run the key signal scrapers and score a single restaurant.

    Runs Google Places, health inspections, TABC, and hours monitor to populate
    raw_signals, then calls compute_scores_v2.

    Returns the scores dict or None on failure.
    """
    try:
        scrape_place(google_place_id, restaurant_id, session)
    except Exception as exc:
        logger.warning("Google Places scrape failed for %s: %s", name, exc)

    try:
        scrape_inspections(name, city, restaurant_id, session)
    except Exception as exc:
        logger.warning("Inspection scrape failed for %s: %s", name, exc)

    try:
        scrape_license(name, city, restaurant_id, session)
    except Exception as exc:
        logger.warning("TABC scrape failed for %s: %s", name, exc)

    try:
        scrape_hours(restaurant_id, session)
    except Exception as exc:
        logger.warning("Hours monitor failed for %s: %s", name, exc)

    try:
        return compute_scores_v2(restaurant_id, session)
    except Exception as exc:
        logger.error("Scoring failed for %s: %s", name, exc)
        return None


# ── Cohort table upsert ───────────────────────────────────────────────────────

def _insert_cohort_record(restaurant_id: str, scores: dict, session: Session) -> None:
    """Write one prospective cohort entry if not already present."""
    exists = session.execute(
        text(
            "SELECT 1 FROM backtest_cohort "
            "WHERE restaurant_id = :rid AND cohort_type = 'prospective' LIMIT 1"
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    if exists:
        return

    overall = scores.get("overall_score", 0)
    session.execute(
        text(
            """
            INSERT INTO backtest_cohort
              (restaurant_id, cohort_type, baseline_score, baseline_risk_band,
               baseline_date, baseline_factors)
            VALUES
              (:restaurant_id, 'prospective', :score, :band, :today, CAST(:factors AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "score":         overall,
            "band":          risk_band(overall),
            "today":         date.today().isoformat(),
            "factors":       json.dumps(scores.get("score_factors", {})),
        },
    )
    session.commit()


# ── Main entry point ──────────────────────────────────────────────────────────

def build_cohort(cities: Optional[list[str]], session: Session) -> dict:
    """Discover, onboard, score, and write restaurants to backtest_cohort.

    Args:
        cities: DFW city list to search. Defaults to all 10 if None.
        session: Active SQLAlchemy session.

    Returns a summary dict with risk band distribution and total count.
    """
    google_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    target_cities = cities or _DFW_CITIES

    band_counts: dict[str, int] = {"low": 0, "moderate": 0, "elevated": 0, "high": 0}
    processed = 0

    # ── Step 1: Standard restaurant search (likely to produce low/moderate risk) ──
    for city in target_cities:
        candidates = _find_google_restaurants(city, google_key)
        for candidate in candidates:
            if not candidate.get("google_place_id"):
                continue
            restaurant_id = _upsert_restaurant(candidate, session)
            scores = _scrape_and_score(
                restaurant_id,
                candidate["google_place_id"],
                candidate["name"],
                city,
                session,
            )
            if scores:
                _insert_cohort_record(restaurant_id, scores, session)
                band = risk_band(scores["overall_score"])
                band_counts[band] += 1
                processed += 1
                logger.info(
                    "[cohort] %s — score=%d band=%s",
                    candidate["name"], scores["overall_score"], band,
                )

    # ── Step 2: Targeted low-scoring restaurant searches ─────────────────────────
    # Dallas OpenData: inspection scores below 70.
    low_insp = _find_low_inspection_restaurants("Dallas")
    for candidate in low_insp:
        # Resolve Google Place ID via text search.
        if google_key and not candidate.get("google_place_id"):
            candidate = _resolve_place_id(candidate, google_key)
        if not candidate.get("google_place_id"):
            continue
        restaurant_id = _upsert_restaurant(candidate, session)
        scores = _scrape_and_score(
            restaurant_id,
            candidate["google_place_id"],
            candidate["name"],
            candidate["city"],
            session,
        )
        if scores:
            _insert_cohort_record(restaurant_id, scores, session)
            band = risk_band(scores["overall_score"])
            band_counts[band] += 1
            processed += 1

    # TABC suspended/reinstated licences.
    tabc_at_risk = _find_tabc_at_risk(target_cities)
    for candidate in tabc_at_risk:
        if google_key and not candidate.get("google_place_id"):
            candidate = _resolve_place_id(candidate, google_key)
        if not candidate.get("google_place_id"):
            continue
        restaurant_id = _upsert_restaurant(candidate, session)
        scores = _scrape_and_score(
            restaurant_id,
            candidate["google_place_id"],
            candidate["name"],
            candidate["city"],
            session,
        )
        if scores:
            _insert_cohort_record(restaurant_id, scores, session)
            band = risk_band(scores["overall_score"])
            band_counts[band] += 1
            processed += 1

    # Google Places: low-rated restaurants (below 3.5 with fewer than 50 reviews).
    for city in target_cities:
        low_rated = _find_google_restaurants(
            city, google_key, min_rating=3.5, max_reviews=50
        )
        for candidate in low_rated:
            if not candidate.get("google_place_id"):
                continue
            restaurant_id = _upsert_restaurant(candidate, session)
            scores = _scrape_and_score(
                restaurant_id,
                candidate["google_place_id"],
                candidate["name"],
                city,
                session,
            )
            if scores:
                _insert_cohort_record(restaurant_id, scores, session)
                band = risk_band(scores["overall_score"])
                band_counts[band] += 1
                processed += 1

    summary = {
        "cities_searched":   target_cities,
        "restaurants_added": processed,
        "band_counts":       band_counts,
    }
    logger.info("Cohort builder complete: %s", summary)
    return summary


def _resolve_place_id(candidate: dict, api_key: str) -> dict:
    """Use Google Places Text Search to find a Google Place ID for a candidate.

    Also checks the returned place types — if Google classifies the resolved
    place as a non-restaurant (liquor store, convenience store, etc.) the
    place ID is withheld so the caller's google_place_id guard skips it.
    """
    query = f"{candidate['name']} {candidate.get('address', '')} {candidate.get('city', '')} TX"
    try:
        resp = httpx.post(
            _GOOGLE_SEARCH_URL,
            headers={
                "X-Goog-Api-Key":   api_key,
                "X-Goog-FieldMask": "places.id,places.businessStatus,places.types",
            },
            json={"textQuery": query, "maxResultCount": 1},
            timeout=15.0,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if places and places[0].get("businessStatus") == "OPERATIONAL":
            place_types = places[0].get("types", [])
            if any(t in NON_RESTAURANT_TYPES for t in place_types):
                logger.debug(
                    "[cohort] Skipping %s — Google types %s indicate non-restaurant",
                    candidate["name"], place_types,
                )
                return candidate  # google_place_id stays None → caller skips it
            candidate = dict(candidate)
            candidate["google_place_id"] = places[0]["id"]
    except Exception:
        pass
    return candidate
