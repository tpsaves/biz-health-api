"""TABC liquor license monitor — Texas Open Data Portal.

Covers all Texas cities. The dataset (kguh-7q9z) is state-wide, so Arlington,
Plano, Frisco, McKinney, Denton, Irving, Garland, and Grand Prairie are all
included alongside Dallas and Fort Worth without any city-specific filtering.

City is included in the search query to improve match accuracy when a chain
restaurant name appears in multiple cities (e.g. "Chili's" in every DFW city).
The matched city from each TABC record is logged so callers can confirm results
came from the intended location.
"""

import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Texas Open Data Portal — TABC Licenses (dataset kguh-7q9z).
# Public, no authentication required; Socrata SODA API.
_TABC_LICENSES_URL = "https://data.texas.gov/resource/kguh-7q9z.json"

# Score per license type. Higher-tier permits require more TABC vetting,
# so their presence is a stronger operational health signal.
_LICENSE_TYPE_SCORES: dict[str, int] = {
    "MB": 100,  # Mixed Beverage Permit — full liquor service
    "BG": 85,   # Beer and Wine Retailer's Permit — table service
    "BQ": 75,   # Beer and Wine Retailer's Off-Premise Permit
    "P":  70,   # Caterer's Permit
}
_DEFAULT_LICENSE_SCORE = 65  # Any other active license type


def scrape_license(trade_name: str, city: str, restaurant_id: str, session: Session) -> dict:
    """Fetch TABC license records from Texas Open Data and write to raw_signals.

    Presence of an active license signals the business is authorized to operate
    as intended. License type (MB > BG > BQ) provides additional scoring nuance.
    Absence of a license is a meaningful signal (unlicensed or expired),
    not a scrape failure — the empty-records payload is stored explicitly.

    Args:
        trade_name:     Business name as listed with TABC (aimstradename field).
        city:           City used to narrow results when the name is not unique.
        restaurant_id:  DB primary key for the restaurant row.
        session:        Active SQLAlchemy session.
    """
    headers = {}
    app_token = os.environ.get("SODA_APP_TOKEN")
    if app_token:
        headers["X-App-Token"] = app_token

    resp = httpx.get(
        _TABC_LICENSES_URL,
        params={
            "$where": (
                # SoQL single-quote escaping: "Torchy's" → "TORCHY''S"
                f"upper(aimstradename) like "
                f"'%{trade_name.upper().replace(chr(39), chr(39) * 2)}%' "
                f"AND upper(city) like "
                f"'%{city.upper().replace(chr(39), chr(39) * 2)}%'"
            ),
            "$limit": "10",
        },
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    records = resp.json()

    # Log the city field from each returned record so it's easy to verify the
    # query hit the right location (important for chain restaurants in multiple cities).
    if records:
        matched_cities = list({r.get("city", "") for r in records})
        logger.info(
            "Stored tabc_license signal — trade_name=%r city_query=%r cities_matched=%s "
            "license_type=%s license_id=%s",
            trade_name,
            city,
            matched_cities,
            records[0].get("aimslicensetype"),
            records[0].get("aimslicenseid"),
        )
    else:
        logger.warning("No TABC license found for trade_name=%r city=%r", trade_name, city)

    # No license records is a meaningful signal, not an error.
    # Storing an empty list lets the scoring engine distinguish no-license from no-scrape.
    payload = {
        "records": records,
        "trade_name_query": trade_name,
        "city_query": city,
        "city_matched": records[0].get("city") if records else None,
    }

    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source": "tabc_license",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    return payload
