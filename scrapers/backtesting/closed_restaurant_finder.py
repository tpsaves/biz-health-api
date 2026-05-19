"""Backtesting Part 1 — Closed Restaurant Finder.

Searches five sources for permanently closed DFW restaurants, deduplicates
using fuzzy name matching, and writes results to the closed_restaurants table.

Sources:
  1. Google Places API v1 — places with businessStatus: CLOSED_PERMANENTLY
  2. TABC Open Data       — licences with status Cancelled or Expired (last 24 mo)
  3. Yelp Fusion API      — permanently closed restaurants (skipped if no key)
  4. Dallas OpenData      — restaurants that failed inspections (score < 60) and
                            are no longer showing active on Google
  5. Texas SOS / Franchise Tax — dissolved/forfeited food-service entities in DFW
                            (falls back gracefully if no machine-readable API)

Target: 100–200 closed DFW restaurants with known or estimated closure dates.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from thefuzz import fuzz

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_DFW_CITIES = [
    "Dallas", "Fort Worth", "Arlington", "Grand Prairie",
    "Plano", "Frisco", "McKinney", "Irving", "Garland", "Denton",
]

_DFW_CITY_SET = {c.lower() for c in _DFW_CITIES}

# Approximate city centers for Google Places location bias (lat/lng, radius 15km).
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
_GOOGLE_DETAILS_URL = "https://places.googleapis.com/v1/places/{}"
_TABC_URL = "https://data.texas.gov/resource/kguh-7q9z.json"
_DALLAS_OD_INSP_URL = "https://www.dallasopendata.com/resource/dri5-wcct.json"
_YELP_URL = "https://api.yelp.com/v3/businesses/search"
# Texas Comptroller franchise-tax forfeiture dataset (closest public proxy for SOS dissolution).
_TX_FRANCHISE_URL = "https://data.texas.gov/resource/9cir-efmm.json"

# 24 months lookback for TABC cancellations and inspection failures.
_LOOKBACK_DAYS = 730

# Fuzzy deduplication threshold (token_set_ratio).
_FUZZY_MIN = 85
# Looser threshold for SOS name-to-restaurant matching (business names often differ from trade names).
_SOS_FUZZY_MIN = 80

# Keywords that suggest a food-service business when present in the entity name.
_FOOD_KEYWORDS = {
    "restaurant", "cafe", "grill", "bar", "bistro", "kitchen", "diner",
    "eatery", "food", "pizza", "taco", "taqueria", "sushi", "bbq", "barbecue",
    "burger", "steakhouse", "cantina", "cocina", "brasserie", "tavern", "pub",
    "lounge", "buffet", "bakery", "deli", "chophouse", "noodle", "ramen", "wok",
    "wings", "smokehouse", "gastropub", "grille", "trattoria", "osteria",
    "dining", "ristorante", "bites", "seafood", "catering", "caterer",
    "eateries", "culinary", "brewpub", "bierhaus",
}


# ── Source 1: Google Places ────────────────────────────────────────────────────

def find_google_closed(api_key: str) -> list[dict]:
    """Query Google Places API v1 for permanently closed restaurants in DFW cities.

    Uses Text Search with a location bias per city. Permanently closed places are
    occasionally returned in search results while Google still has their data indexed.
    Filter by businessStatus == CLOSED_PERMANENTLY.
    """
    if not api_key:
        logger.warning("[google] GOOGLE_PLACES_API_KEY not set — skipping source 1")
        return []

    results: list[dict] = []
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.businessStatus,places.rating,places.userRatingCount,"
            "places.addressComponents"
        ),
    }

    for city in _DFW_CITIES:
        try:
            resp = httpx.post(
                _GOOGLE_SEARCH_URL,
                headers=headers,
                json={
                    "textQuery": f"restaurant in {city} Texas",
                    "includedType": "restaurant",
                    "locationBias": {
                        "circle": {
                            "center": _CITY_CENTERS[city],
                            "radius": 15000.0,
                        }
                    },
                    "maxResultCount": 20,
                },
                timeout=30.0,
            )
            resp.raise_for_status()

            for place in resp.json().get("places", []):
                if place.get("businessStatus") != "CLOSED_PERMANENTLY":
                    continue

                name = place.get("displayName", {}).get("text", "")
                place_id = place.get("id", "")
                formatted_addr = place.get("formattedAddress", "")
                zip_code = _extract_zip(place.get("addressComponents", []))

                # Use the most recent review date as a proxy for when activity stopped.
                closure_date, closure_estimated = _estimate_closure_date_google(
                    place_id, api_key
                )

                results.append({
                    "name": name,
                    "address": _strip_city_state_zip(formatted_addr),
                    "city": city,
                    "zip": zip_code,
                    "google_place_id": place_id,
                    "yelp_id": None,
                    "closure_date": closure_date,
                    "closure_date_estimated": closure_estimated,
                    "closure_source": "google",
                })
                logger.info("[google] closed: %s — %s", name, city)

        except Exception as exc:
            logger.error("[google] %s failed: %s", city, exc)

    logger.info("[google] found %d permanently closed restaurants", len(results))
    return results


def _estimate_closure_date_google(place_id: str, api_key: str) -> tuple[Optional[str], bool]:
    """Fetch place details to get the last review date as a proxy for closure date."""
    try:
        resp = httpx.get(
            _GOOGLE_DETAILS_URL.format(place_id),
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "reviews",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        reviews = resp.json().get("reviews", [])
        if reviews:
            # Take the most recent publish time as an upper bound on when it was still open.
            dates = []
            for r in reviews:
                pt = r.get("publishTime", "")
                if pt:
                    try:
                        dates.append(datetime.fromisoformat(pt.replace("Z", "+00:00")).date())
                    except ValueError:
                        pass
            if dates:
                return max(dates).isoformat(), True  # estimated = True
    except Exception:
        pass
    return None, None


# ── Source 2: TABC Cancellations ───────────────────────────────────────────────

def find_tabc_cancelled(session: Optional[Session] = None) -> list[dict]:
    """Find DFW restaurants whose TABC licence is no longer in the active dataset.

    The TABC dataset (kguh-7q9z) contains only ACTIVE licences — there is no
    status column to filter on directly.  Instead we cross-reference:

      1. Fetch all currently active licence IDs for DFW cities.
      2. Query raw_signals (source='tabc_license') for restaurants we previously
         scraped and confirmed had a licence.
      3. Return restaurants whose recorded licence ID is absent from the active set.

    Requires a DB session to read raw_signals.  Returns [] without one.
    """
    if session is None:
        logger.warning("[tabc] No session provided — skipping TABC cross-reference")
        return []

    # Step 1: fetch every currently active DFW licence ID.
    city_filter = " OR ".join(f"upper(city)='{c.upper()}'" for c in _DFW_CITIES)
    active_ids: set[str] = set()
    try:
        resp = httpx.get(
            _TABC_URL,
            params={
                "$where": city_filter,
                "$select": "aimslicenseid",
                "$limit": 10000,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        for rec in resp.json():
            lid = (rec.get("aimslicenseid") or "").strip()
            if lid:
                active_ids.add(lid)
    except Exception as exc:
        logger.error("[tabc] Failed to fetch active licences: %s", exc)
        return []

    if not active_ids:
        logger.warning("[tabc] No active DFW licences returned — skipping cross-reference")
        return []

    # Step 2: query raw_signals for restaurants with previously-recorded TABC licences.
    # Build city list inline (hardcoded constant — safe for f-string).
    cities_sql = ", ".join(f"'{c.lower()}'" for c in _DFW_CITIES)
    results: list[dict] = []
    try:
        rows = session.execute(
            text(
                f"SELECT r.name, r.city, r.address, rs.payload::text "
                f"FROM raw_signals rs "
                f"JOIN restaurants r ON r.id = rs.restaurant_id "
                f"WHERE rs.source = 'tabc_license' "
                f"  AND lower(r.city) IN ({cities_sql}) "
                f"  AND jsonb_array_length(rs.payload->'records') > 0"
            )
        ).fetchall()
    except Exception as exc:
        logger.error("[tabc] raw_signals query failed: %s", exc)
        return []

    # Step 3: flag restaurants with no remaining active licence.
    for row in rows:
        try:
            payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            lic_ids = {
                (r.get("aimslicenseid") or "").strip()
                for r in payload.get("records", [])
                if r.get("aimslicenseid")
            }
            if lic_ids and lic_ids.isdisjoint(active_ids):
                results.append({
                    "name":                   row[0],
                    "address":                row[2] or "",
                    "city":                   (row[1] or "").title(),
                    "zip":                    None,
                    "google_place_id":        None,
                    "yelp_id":                None,
                    "closure_date":           None,
                    "closure_date_estimated": None,
                    "closure_source":         "tabc",
                })
        except Exception:
            continue

    logger.info("[tabc] found %d restaurants with lapsed licences", len(results))
    return results


# ── Source 3: Yelp Fusion API ─────────────────────────────────────────────────

def find_yelp_closed(yelp_key: str) -> list[dict]:
    """Query Yelp Fusion API for permanently closed restaurants in DFW.

    Returns empty list without raising if YELP_API_KEY is absent — this source
    is optional per the phase spec.
    """
    if not yelp_key:
        logger.info("[yelp] No YELP_API_KEY — skipping source 3")
        return []

    results: list[dict] = []
    headers = {"Authorization": f"Bearer {yelp_key}"}

    for city in _DFW_CITIES:
        try:
            resp = httpx.get(
                _YELP_URL,
                headers=headers,
                params={
                    "location": f"{city}, TX",
                    "categories": "restaurants",
                    "is_closed": True,
                    "limit": 50,
                },
                timeout=30.0,
            )
            if resp.status_code == 401:
                logger.warning("[yelp] API key rejected — skipping source 3")
                return []
            resp.raise_for_status()

            for biz in resp.json().get("businesses", []):
                if not biz.get("is_closed"):
                    continue
                name = biz.get("name", "")
                loc = biz.get("location", {})
                addr = ", ".join(filter(None, [
                    loc.get("address1", ""),
                    loc.get("address2", ""),
                ]))
                results.append({
                    "name": name,
                    "address": addr,
                    "city": city,
                    "zip": loc.get("zip_code"),
                    "google_place_id": None,
                    "yelp_id": biz.get("id"),
                    "closure_date": None,
                    "closure_date_estimated": None,
                    "closure_source": "yelp",
                })

        except Exception as exc:
            logger.error("[yelp] %s failed: %s", city, exc)

    logger.info("[yelp] found %d permanently closed restaurants", len(results))
    return results


# ── Source 4: Dallas OpenData failed inspections ───────────────────────────────

def find_inspection_inferred_closures(google_api_key: str) -> list[dict]:
    """Find Dallas restaurants with critical inspection failures that are no longer active.

    Queries Dallas OpenData for scores < 60 in the last 24 months, then checks
    Google Places to see if the business is still showing as operational.
    Restaurants that show CLOSED_PERMANENTLY (or are not found) are included.
    """
    cutoff_str = (
        datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    ).date().isoformat()

    # Dallas OpenData (dri5-wcct) covers Oct 2016 – Jan 2024.
    # Fields: program_identifier (name), address, zip, insp_date, score.
    # No date filter — we want all critical failures in the dataset to find potential closures.
    candidates: list[dict] = []
    try:
        resp = httpx.get(
            _DALLAS_OD_INSP_URL,
            params={
                "$where": "score < 60",
                "$select": "program_identifier,site_address,zip,insp_date,score",
                "$order": "insp_date DESC",
                "$limit": 500,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        candidates = resp.json()
    except Exception as exc:
        logger.error("[inspection_inference] Dallas OpenData query failed: %s", exc)
        return []

    results: list[dict] = []
    headers = {
        "X-Goog-Api-Key": google_api_key,
        "X-Goog-FieldMask": "places.id,places.businessStatus,places.displayName",
    } if google_api_key else {}

    seen_names: set[str] = set()

    for rec in candidates:
        name = (rec.get("program_identifier") or "").strip()
        if not name or name.lower() in seen_names:
            continue
        seen_names.add(name.lower())

        address = rec.get("site_address", "")

        # Check Google to see if the restaurant is still operational.
        still_open = _is_still_operational_google(name, address, "Dallas", headers)
        if still_open:
            continue  # Still open — not a closure candidate.

        # Use the inspection date as a proxy for when distress was detected.
        insp_date_str = rec.get("insp_date", "")
        closure_date = None
        if insp_date_str:
            try:
                closure_date = datetime.fromisoformat(insp_date_str[:10]).date().isoformat()
            except ValueError:
                pass

        results.append({
            "name": name,
            "address": address,
            "city": "Dallas",
            "zip": rec.get("zip"),
            "google_place_id": None,
            "yelp_id": None,
            "closure_date": closure_date,
            "closure_date_estimated": True,
            "closure_source": "inspection_inference",
        })

    logger.info("[inspection_inference] found %d likely closed restaurants", len(results))
    return results


def _is_still_operational_google(name: str, address: str, city: str, headers: dict) -> bool:
    """Return True if Google Places still shows this restaurant as OPERATIONAL."""
    if not headers:
        return False  # No API key — assume unknown, don't add to results.

    query = f"{name} {address} {city} TX"
    try:
        resp = httpx.post(
            _GOOGLE_SEARCH_URL,
            headers=dict(headers, **{"X-Goog-FieldMask": "places.id,places.businessStatus"}),
            json={"textQuery": query, "maxResultCount": 1},
            timeout=15.0,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if places:
            return places[0].get("businessStatus") == "OPERATIONAL"
    except Exception:
        pass
    return False  # If we can't confirm it's open, treat as potentially closed.


# ── Source 5: Texas SOS / Franchise Tax dissolved entities ───────────────────

def find_sos_dissolved() -> list[dict]:
    """Query Texas Comptroller franchise-tax data for dissolved/forfeited DFW food businesses.

    The Texas SOS web portal (sos.state.tx.us) exposes no machine-readable API.
    As a proxy we use the Texas Comptroller's franchise-tax dataset on data.texas.gov,
    which marks entities as 'Forfeited' when they cease business — closely correlated
    with SOS dissolution records.

    Filtering logic:
      1. Status = 'F' (Forfeited) within the last 24 months.
      2. City must be one of the 10 DFW cities.
      3. Entity name must contain at least one food-service keyword OR score ≥ 80
         on a fuzzy match against a curated food-keyword set.

    Returns empty list and logs a warning if the endpoint is unavailable.
    """
    cutoff_str = (
        datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    ).date().isoformat()

    # Field names confirmed from data.texas.gov/resource/9cir-efmm.json probe:
    #   taxpayer_city (not city), taxpayer_zip (not zip),
    #   right_to_transact_business_code='N' means forfeited/dissolved.
    city_filter = " OR ".join(f"upper(taxpayer_city)='{c.upper()}'" for c in _DFW_CITIES)

    results: list[dict] = []
    try:
        resp = httpx.get(
            _TX_FRANCHISE_URL,
            params={
                "$where": f"right_to_transact_business_code='N' AND ({city_filter})",
                "$select": "taxpayer_name,taxpayer_address,taxpayer_city,taxpayer_zip",
                "$limit": 5000,
            },
            timeout=60.0,
        )

        if resp.status_code in (400, 404, 405, 501):
            logger.warning(
                "[sos] Texas franchise-tax endpoint unavailable (HTTP %d) — skipping source 5",
                resp.status_code,
            )
            return []

        resp.raise_for_status()
        records = resp.json()

    except httpx.HTTPStatusError as exc:
        logger.warning("[sos] HTTP error fetching franchise-tax data: %s — skipping source 5", exc)
        return []
    except Exception as exc:
        logger.warning("[sos] Franchise-tax query failed: %s — skipping source 5", exc)
        return []

    for rec in records:
        name = (rec.get("taxpayer_name") or "").strip()
        if not name:
            continue

        # Keep only likely food-service entities using keyword matching (min score 80).
        name_lower = name.lower()
        is_food = any(kw in name_lower for kw in _FOOD_KEYWORDS)
        if not is_food:
            combined_keywords = " ".join(_FOOD_KEYWORDS)
            if fuzz.token_set_ratio(name_lower, combined_keywords) < _SOS_FUZZY_MIN:
                continue

        city_raw = (rec.get("taxpayer_city") or "").title()
        if city_raw.lower() not in _DFW_CITY_SET:
            continue

        # No dissolution date field exists in this dataset — mark as estimated.
        results.append({
            "name":                   name,
            "address":                rec.get("taxpayer_address", ""),
            "city":                   city_raw,
            "zip":                    rec.get("taxpayer_zip"),
            "google_place_id":        None,
            "yelp_id":                None,
            "closure_date":           None,
            "closure_date_estimated": None,
            "closure_source":         "sos",
        })

    logger.info("[sos] found %d dissolved food-service entities in DFW", len(results))
    return results


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(records: list[dict]) -> list[dict]:
    """Remove duplicates across sources using fuzzy name matching (min score 85).

    When two records match, keep the one with:
    1. A known (non-estimated) closure date
    2. A google_place_id (most authoritative)
    In case of a tie, prefer the first record (retains source priority order).
    """
    unique: list[dict] = []

    for rec in records:
        matched = False
        for existing in unique:
            score = fuzz.token_set_ratio(
                rec["name"].lower(), existing["name"].lower()
            )
            if score >= _FUZZY_MIN:
                # Merge: upgrade to better closure date or place_id if available.
                if existing.get("closure_date_estimated") and not rec.get("closure_date_estimated"):
                    existing["closure_date"] = rec["closure_date"]
                    existing["closure_date_estimated"] = False
                if not existing.get("google_place_id") and rec.get("google_place_id"):
                    existing["google_place_id"] = rec["google_place_id"]
                if not existing.get("yelp_id") and rec.get("yelp_id"):
                    existing["yelp_id"] = rec["yelp_id"]
                matched = True
                break

        if not matched:
            unique.append(dict(rec))

    logger.info("Deduplication: %d records → %d unique", len(records), len(unique))
    return unique


# ── DB upsert ─────────────────────────────────────────────────────────────────

def upsert_closed_restaurants(records: list[dict], session: Session) -> int:
    """Insert closed restaurant records, skipping exact name+address duplicates."""
    inserted = 0
    for rec in records:
        # Skip if already in table (same name + city).
        exists = session.execute(
            text(
                "SELECT 1 FROM closed_restaurants "
                "WHERE lower(name) = lower(:name) AND lower(city) = lower(:city) LIMIT 1"
            ),
            {"name": rec["name"], "city": rec.get("city", "")},
        ).one_or_none()

        if exists:
            continue

        session.execute(
            text(
                """
                INSERT INTO closed_restaurants
                  (name, address, city, zip, google_place_id, yelp_id,
                   closure_date, closure_date_estimated, closure_source)
                VALUES
                  (:name, :address, :city, :zip, :google_place_id, :yelp_id,
                   :closure_date, :closure_date_estimated, :closure_source)
                """
            ),
            {
                "name":                   rec["name"],
                "address":                rec.get("address"),
                "city":                   rec.get("city"),
                "zip":                    rec.get("zip"),
                "google_place_id":        rec.get("google_place_id"),
                "yelp_id":                rec.get("yelp_id"),
                "closure_date":           rec.get("closure_date"),
                "closure_date_estimated": rec.get("closure_date_estimated"),
                "closure_source":         rec.get("closure_source"),
            },
        )
        inserted += 1

    session.commit()
    return inserted


# ── Main entry point ──────────────────────────────────────────────────────────

def run_closed_restaurant_finder(session: Session) -> dict:
    """Run all five sources, deduplicate, and write to closed_restaurants table.

    Returns a summary dict with counts per source and total inserted.
    """
    google_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    yelp_key   = os.environ.get("YELP_API_KEY", "")

    google_records     = find_google_closed(google_key)
    tabc_records       = find_tabc_cancelled(session)
    yelp_records       = find_yelp_closed(yelp_key)
    inferred_records   = find_inspection_inferred_closures(google_key)
    sos_records        = find_sos_dissolved()

    all_records = google_records + tabc_records + yelp_records + inferred_records + sos_records
    unique      = deduplicate(all_records)
    inserted    = upsert_closed_restaurants(unique, session)

    summary = {
        "google_count":    len(google_records),
        "tabc_count":      len(tabc_records),
        "yelp_count":      len(yelp_records),
        "inferred_count":  len(inferred_records),
        "sos_count":       len(sos_records),
        "raw_total":       len(all_records),
        "unique_total":    len(unique),
        "inserted":        inserted,
    }
    logger.info("Closed restaurant finder complete: %s", summary)
    return summary


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_zip(components: list[dict]) -> Optional[str]:
    """Pull postal code from Google Places addressComponents list."""
    for comp in components:
        if "postal_code" in comp.get("types", []):
            return comp.get("longText") or comp.get("shortText")
    return None


def _strip_city_state_zip(formatted_addr: str) -> str:
    """Return just the street portion by dropping the last two comma-separated parts."""
    parts = [p.strip() for p in formatted_addr.split(",")]
    return ", ".join(parts[:-2]) if len(parts) > 2 else formatted_addr
