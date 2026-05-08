import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def scrape_hours(restaurant_id: str, session: Session) -> dict:
    """Extract and normalize opening hours from the most recent Google Places signal.

    This is a pure DB-read processing step — no external API call. It reads the
    opening_hours block already stored in raw_signals by the Google Places scraper,
    computes a completeness score, and writes a new row with source='hours_monitor'.

    In C# this would be a separate hosted service or background job reading from the
    same table; here it's just a function called from the test script.
    """
    row = session.execute(
        text(
            """
            SELECT payload
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'google_places'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    if row is None:
        raise RuntimeError(
            f"No google_places signal for restaurant_id={restaurant_id} — "
            "run test_google_places.py first"
        )

    result        = row.payload.get("result", {})
    opening_hours = result.get("opening_hours", {})
    periods       = opening_hours.get("periods", [])
    weekday_text  = opening_hours.get("weekday_text", [])

    # periods[i]["open"]["day"] is 0 (Sunday) through 6 (Saturday).
    # A 24/7 venue has a single entry with no close key; we still count it as 7 days.
    if periods and "close" not in periods[0]:
        # 24/7 operation — one open-only period covers all days
        days_with_hours = 7
    else:
        days_with_hours = len({p["open"]["day"] for p in periods if "open" in p})

    hours_completeness = int(days_with_hours / 7 * 100)

    payload = {
        "days_with_hours": days_with_hours,
        "hours_completeness": hours_completeness,
        "open_now": opening_hours.get("open_now"),
        "weekday_text": weekday_text,
        "periods_count": len(periods),
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
            "source": "hours_monitor",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    logger.info(
        "Stored hours_monitor signal — days=%s completeness=%s open_now=%s",
        days_with_hours,
        hours_completeness,
        opening_hours.get("open_now"),
    )
    return payload
