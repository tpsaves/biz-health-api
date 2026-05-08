import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_FIELDS = "name,rating,user_ratings_total,reviews,opening_hours"


def scrape_place(place_id: str, restaurant_id: str, session: Session) -> dict:
    """Fetch Google Places details and write raw payload to raw_signals.

    SQLAlchemy sessions work like EF Core DbContext: call commit() to flush
    to the DB. Unlike EF Core, there's no change tracker — we write raw SQL
    via session.execute(text(...)).
    """
    api_key = os.environ["GOOGLE_PLACES_API_KEY"]

    response = httpx.get(
        _DETAIL_URL,
        params={"place_id": place_id, "fields": _FIELDS, "key": api_key},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(
            f"Google Places API returned status {payload.get('status')!r} "
            f"for place_id={place_id}"
        )

    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source": "google_places",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    result = payload.get("result", {})
    logger.info(
        "Stored Google Places signal — place_id=%s rating=%s reviews=%s",
        place_id,
        result.get("rating"),
        result.get("user_ratings_total"),
    )
    return payload
