import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Texas Open Data Portal — TABC Licenses (dataset kguh-7q9z).
# Contains all primary licenses issued by the Texas Alcoholic Beverage Commission.
# Public, no authentication required; Socrata SODA API.
_TABC_LICENSES_URL = "https://data.texas.gov/resource/kguh-7q9z.json"

# Score assigned per license type. Higher-tier permits require more vetting from TABC,
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

    Presence of an active license record signals the business is authorized to operate
    as intended. License type (MB > BG > BQ) provides additional scoring nuance.
    """
    headers = {}
    app_token = os.environ.get("SODA_APP_TOKEN")
    if app_token:
        headers["X-App-Token"] = app_token

    resp = httpx.get(
        _TABC_LICENSES_URL,
        params={
            "$where": (
                f"upper(aimstradename) like '%{trade_name.upper()}%' "
                f"AND upper(city)='{city.upper()}'"
            ),
            "$limit": "10",
        },
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    records = resp.json()

    # No records is a meaningful signal (license absent or expired), not an error.
    # Store an empty list so the scoring engine can distinguish no-license from no-scrape.
    payload = {"records": records, "trade_name_query": trade_name, "city_query": city}

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

    if records:
        first = records[0]
        logger.info(
            "Stored tabc_license signal — trade_name=%s license_type=%s license_id=%s",
            first.get("aimstradename"),
            first.get("aimslicensetype"),
            first.get("aimslicenseid"),
        )
    else:
        logger.warning("No TABC license found for trade_name=%r city=%r", trade_name, city)

    return payload
