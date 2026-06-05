import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _compute_weekly_hours(periods: list[dict]) -> float | None:
    """Compute total weekly operating hours from Google Places periods."""
    if not periods:
        return None
    # 24/7: single open-only entry with no close key
    if len(periods) == 1 and "close" not in periods[0]:
        return round(24.0 * 7, 1)
    total_minutes = 0
    for p in periods:
        if "open" not in p or "close" not in p:
            continue
        open_m  = p["open"]["hour"] * 60 + p["open"].get("minute", 0)
        close_m = p["close"]["hour"] * 60 + p["close"].get("minute", 0)
        if close_m < open_m:   # overnight span (e.g. 10 PM – 2 AM)
            close_m += 24 * 60
        duration = close_m - open_m
        if duration > 0:
            total_minutes += duration
    return round(total_minutes / 60, 1) if total_minutes > 0 else None


def scrape_hours(restaurant_id: str, session: Session) -> dict:
    """Extract and normalize opening hours from the most recent Google Places signal.

    Reads opening_hours.periods to compute total_weekly_hours and avg_hours_per_day.
    Compares against the prior hours_monitor snapshot to detect hours reductions.
    """
    row = session.execute(
        text("""
            SELECT payload
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'google_places'
            ORDER BY scraped_at DESC
            LIMIT 1
        """),
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

    # days_with_hours — 24/7 venues have a single open-only period
    if periods and "close" not in periods[0]:
        days_with_hours = 7
    else:
        days_with_hours = len({p["open"]["day"] for p in periods if "open" in p})

    hours_completeness = int(days_with_hours / 7 * 100)

    # total weekly hours from period durations
    total_weekly_hours = _compute_weekly_hours(periods)
    avg_hours_per_day  = (
        round(total_weekly_hours / days_with_hours, 1)
        if total_weekly_hours is not None and days_with_hours > 0
        else None
    )

    # compare against most recent prior snapshot to detect reductions
    prior_row = session.execute(
        text("""
            SELECT payload FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'hours_monitor'
            ORDER BY scraped_at DESC
            LIMIT 1
        """),
        {"rid": restaurant_id},
    ).one_or_none()

    hours_reduction_pct: float | None = None
    if (
        prior_row is not None
        and prior_row.payload.get("total_weekly_hours") is not None
        and total_weekly_hours is not None
    ):
        prior_weekly = prior_row.payload["total_weekly_hours"]
        if prior_weekly > 0:
            reduction = (prior_weekly - total_weekly_hours) / prior_weekly * 100
            hours_reduction_pct = round(reduction, 1)

    payload = {
        "days_with_hours":    days_with_hours,
        "hours_completeness": hours_completeness,
        "open_now":           opening_hours.get("open_now"),
        "weekday_text":       weekday_text,
        "periods_count":      len(periods),
        "total_weekly_hours": total_weekly_hours,
        "avg_hours_per_day":  avg_hours_per_day,
        "hours_reduction_pct": hours_reduction_pct,
    }

    session.execute(
        text("""
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
        """),
        {"restaurant_id": restaurant_id, "source": "hours_monitor", "payload": json.dumps(payload)},
    )
    session.commit()

    logger.info(
        "Stored hours_monitor signal — days=%s completeness=%s "
        "total_weekly_hours=%s hours_reduction_pct=%s open_now=%s",
        days_with_hours, hours_completeness,
        total_weekly_hours, hours_reduction_pct, opening_hours.get("open_now"),
    )
    return payload
