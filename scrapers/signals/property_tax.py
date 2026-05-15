"""Business personal property tax delinquency scraper.

Routes to the correct county appraisal district (CAD) based on city.
Most DFW CADs do not expose public REST APIs for their property search portals.
When a CAD portal has no usable API, this scraper logs no_data_available rather
than crashing — the scoring engine treats absent tax data as neutral (no penalty).

CAD routing:
  Dallas, Irving, Garland     → Dallas Central Appraisal District (dallascad.org)
  Fort Worth, Arlington,
    Grand Prairie              → Tarrant Appraisal District (tad.org)
  Plano, Frisco, McKinney     → Collin CAD (collincad.org)
  Denton                      → Denton CAD (dentoncad.com)

Fuzzy matching note for C# developers:
  fuzz.token_sort_ratio() from thefuzz is the Python equivalent of
  FuzzySharp.Fuzz.TokenSortRatio(). It tokenizes, sorts, and compares —
  making it robust to "Lodge Pecan" vs "Pecan Lodge" (both return 100).
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session
from thefuzz import fuzz

logger = logging.getLogger(__name__)

_FUZZY_MIN_SCORE = 80

# City → CAD routing.  handler = internal dispatch key.
_CITY_ROUTES: dict[str, dict] = {
    "dallas": {
        "cad":     "Dallas Central Appraisal District",
        "url":     "https://www.dallascad.org",
        "handler": "dallas_cad",
    },
    "irving": {
        "cad":     "Dallas Central Appraisal District",
        "url":     "https://www.dallascad.org",
        "handler": "dallas_cad",
    },
    "garland": {
        "cad":     "Dallas Central Appraisal District",
        "url":     "https://www.dallascad.org",
        "handler": "dallas_cad",
    },
    "fort worth": {
        "cad":     "Tarrant Appraisal District",
        "url":     "https://www.tad.org",
        "handler": "tarrant_cad",
    },
    "arlington": {
        "cad":     "Tarrant Appraisal District",
        "url":     "https://www.tad.org",
        "handler": "tarrant_cad",
    },
    "grand prairie": {
        "cad":     "Tarrant Appraisal District",
        "url":     "https://www.tad.org",
        "handler": "tarrant_cad",
    },
    "plano": {
        "cad":     "Collin Central Appraisal District",
        "url":     "https://www.collincad.org",
        "handler": "collin_cad",
    },
    "frisco": {
        "cad":     "Collin Central Appraisal District",
        "url":     "https://www.collincad.org",
        "handler": "collin_cad",
    },
    "mckinney": {
        "cad":     "Collin Central Appraisal District",
        "url":     "https://www.collincad.org",
        "handler": "collin_cad",
    },
    "denton": {
        "cad":     "Denton Central Appraisal District",
        "url":     "https://www.dentoncad.com",
        "handler": "denton_cad",
    },
}


def scrape_property_tax(name: str, city: str, restaurant_id: str, session: Session) -> dict:
    """Fetch business personal property tax status for a restaurant.

    Writes results to raw_signals with source='property_tax'.
    Returns the payload dict. When no public API is available for the CAD,
    returns a payload with status='no_data_available' rather than raising.
    """
    logger.info("Property tax scrape: name=%r city=%r", name, city)

    route = _CITY_ROUTES.get(city.lower())
    if route is None:
        logger.warning("No CAD route for city=%r — no_data_available", city)
        return _write_no_data(
            name, city, "unconfigured_city", restaurant_id, session
        )

    cad_name = route["cad"]
    handler  = route["handler"]

    try:
        if handler == "dallas_cad":
            result = _fetch_dallas_cad(name, cad_name)
        elif handler == "tarrant_cad":
            result = _fetch_tarrant_cad(name, cad_name)
        elif handler == "collin_cad":
            result = _fetch_collin_cad(name, cad_name)
        elif handler == "denton_cad":
            result = _fetch_denton_cad(name, cad_name)
        else:
            result = None
    except Exception as exc:
        logger.warning("CAD fetch failed city=%r cad=%r: %s", city, cad_name, exc)
        result = None

    if result is None:
        return _write_no_data(name, city, cad_name, restaurant_id, session)

    payload = {
        "name_query":           name,
        "city":                 city,
        "cad":                  cad_name,
        "status":               result.get("status", "unknown"),
        "delinquent":           result.get("delinquent", False),
        "years_delinquent":     result.get("years_delinquent", 0),
        "delinquent_amount":    result.get("delinquent_amount"),
        "assessed_value":       result.get("assessed_value"),
        "_fuzzy_match_score":   result.get("_fuzzy_match_score"),
        "_matched_name":        result.get("_matched_name"),
    }

    return _write_signal(restaurant_id, "property_tax", payload, session)


# ── per-CAD fetch functions ────────────────────────────────────────────────────
# These CADs do not expose documented public REST/JSON APIs for their BPP (Business
# Personal Property) search portals. Each function logs the reason and returns None
# so the caller records no_data_available.
#
# If a CAD publishes a public API in the future, implement it here without changing
# scrape_property_tax() — the caller contract stays the same.

def _fetch_dallas_cad(name: str, cad: str) -> None:
    """Dallas CAD (dallascad.org) does not expose a public BPP search API."""
    logger.info(
        "Dallas CAD (%s) does not have a public BPP API — no_data_available for %r",
        cad, name,
    )
    return None


def _fetch_tarrant_cad(name: str, cad: str) -> None:
    """Tarrant CAD (tad.org) does not expose a public BPP search API."""
    logger.info(
        "Tarrant CAD (%s) does not have a public BPP API — no_data_available for %r",
        cad, name,
    )
    return None


def _fetch_collin_cad(name: str, cad: str) -> None:
    """Collin CAD (collincad.org) does not expose a public BPP search API."""
    logger.info(
        "Collin CAD (%s) does not have a public BPP API — no_data_available for %r",
        cad, name,
    )
    return None


def _fetch_denton_cad(name: str, cad: str) -> None:
    """Denton CAD (dentoncad.com) does not expose a public BPP search API."""
    logger.info(
        "Denton CAD (%s) does not have a public BPP API — no_data_available for %r",
        cad, name,
    )
    return None


# ── shared helpers ─────────────────────────────────────────────────────────────

def _write_no_data(
    name: str,
    city: str,
    cad: str,
    restaurant_id: str,
    session: Session,
) -> dict:
    payload = {
        "status":            "no_data_available",
        "name_query":        name,
        "city":              city,
        "cad":               cad,
        "delinquent":        False,
        "years_delinquent":  0,
        "delinquent_amount": None,
        "assessed_value":    None,
    }
    return _write_signal(restaurant_id, "property_tax", payload, session)


def _write_signal(restaurant_id: str, source: str, payload: dict, session: Session) -> dict:
    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source":        source,
            "payload":       json.dumps(payload),
        },
    )
    session.commit()
    return payload
