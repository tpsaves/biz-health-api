import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Dallas Open Data — Restaurant and Food Establishment Inspections (dataset dri5-wcct).
# Data runs October 2016 to January 2024 (sunset; current inspections moved to
# inspections.myhealthdepartment.com, which has no public API). Historical records
# are still a valid signal for scoring.
_DALLAS_INSPECTIONS_URL = "https://www.dallasopendata.com/resource/dri5-wcct.json"


def scrape_inspections(name: str, restaurant_id: str, session: Session) -> dict:
    """Fetch health inspection records from Dallas Open Data and write to raw_signals.

    Socrata SODA API allows unauthenticated access at 1000 req/hr. Set SODA_APP_TOKEN
    in .env for a higher anonymous limit — it is optional, not required for dev use.

    The `name` parameter is embedded in SoQL LIKE syntax. It comes from our own DB
    (not user input), so injection risk is low, but keep it out of external-facing code.
    """
    headers = {}
    app_token = os.environ.get("SODA_APP_TOKEN")
    if app_token:
        # X-App-Token raises the anonymous rate limit to ~10 000 req/hr
        headers["X-App-Token"] = app_token

    resp = httpx.get(
        _DALLAS_INSPECTIONS_URL,
        params={
            "$where": f"upper(program_identifier) like '%{name.upper()}%'",
            "$order": "insp_date DESC",
            "$limit": "10",
        },
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    records = resp.json()

    if not records:
        raise RuntimeError(f"No health inspection records found for {name!r} in Dallas Open Data")

    payload = {"records": records, "name_query": name}

    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source": "health_inspections",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    latest = records[0]
    logger.info(
        "Stored health_inspections signal — name=%s latest_score=%s date=%s",
        latest.get("program_identifier"),
        latest.get("score"),
        latest.get("insp_date", "")[:10],
    )
    return payload
