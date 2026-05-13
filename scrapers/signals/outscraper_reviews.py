"""Outscraper Google Maps Reviews scraper.

Fetches up to 12 months of review history per restaurant, enabling true monthly
breakdowns that the Google Places API cannot provide (it returns only 5 reviews max).
Outscraper is pay-per-use, so this runs weekly rather than daily.
"""

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_OUTSCRAPER_URL = "https://api.app.outscraper.com/maps/reviews-v3"
_POLL_URL = "https://api.app.outscraper.com/requests/{}"
_MAX_REVIEWS = 520   # ~12 months for an active restaurant averaging 40 reviews/month
_POLL_RETRIES = 20
_POLL_INTERVAL = 6   # seconds between status polls


def scrape_outscraper_reviews(
    google_place_id: str,
    name: str,
    restaurant_id: str,
    session: Session,
) -> dict:
    """Fetch up to 12 months of Google reviews from Outscraper and write to raw_signals.

    Outscraper returns full review history with timestamps, enabling true monthly
    breakdowns for year-over-year comparison — something the 5-review Google
    Places API snapshot cannot support.

    In C#, you'd model the polling loop as a Task with CancellationToken;
    here we block the calling thread with time.sleep() since APScheduler runs
    jobs in daemon threads and blocking is acceptable for a background job.
    """
    api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OUTSCRAPER_API_KEY not set in environment")

    headers = {"X-API-KEY": api_key}

    # prefix "place_id:" tells Outscraper to treat the value as a Google Place ID
    # rather than a text search query — avoids ambiguous matches.
    resp = httpx.get(
        _OUTSCRAPER_URL,
        params={
            "query": f"place_id:{google_place_id}",
            "limit": 1,              # number of places to look up
            "reviewsLimit": _MAX_REVIEWS,
            "sort": "newest",
            "async": "false",        # request synchronous response when possible
        },
        headers=headers,
        timeout=120.0,
    )
    resp.raise_for_status()
    raw = resp.json()

    # If the server ignored async=false and returned a pending task, poll for results.
    if raw.get("status") == "Pending":
        raw = _poll_for_results(raw.get("id", ""), headers)

    reviews = _extract_reviews(raw)
    monthly_breakdown = _build_monthly_breakdown(reviews)

    payload = {
        "monthly_breakdown": monthly_breakdown,
        "total_reviews_fetched": len(reviews),
        "google_place_id": google_place_id,
        "name": name,
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
            "source": "outscraper_reviews",
            "payload": json.dumps(payload),
        },
    )
    session.commit()

    logger.info(
        "Stored outscraper_reviews signal — name=%s months=%d total_reviews=%d",
        name,
        len(monthly_breakdown),
        len(reviews),
    )
    return payload


def _poll_for_results(request_id: str, headers: dict) -> dict:
    """Poll Outscraper until the async task completes."""
    for attempt in range(_POLL_RETRIES):
        time.sleep(_POLL_INTERVAL)
        resp = httpx.get(
            _POLL_URL.format(request_id),
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
        status = result.get("status")
        if status == "Success":
            return result
        if status == "Failed":
            raise RuntimeError(f"Outscraper task {request_id} failed: {result}")
        logger.debug("Outscraper task %s status=%s attempt=%d", request_id, status, attempt + 1)

    raise RuntimeError(
        f"Outscraper task {request_id} did not complete after {_POLL_RETRIES * _POLL_INTERVAL}s"
    )


def _extract_reviews(raw: dict) -> list[dict]:
    """Pull the flat list of review objects out of the Outscraper response envelope."""
    data = raw.get("data", [])
    if not data:
        return []
    # Response is [[review, ...]] (list of places, each place is a list of reviews)
    first = data[0]
    if isinstance(first, list):
        return first
    # Occasionally data is already a flat list of reviews
    return data


def _build_monthly_breakdown(reviews: list[dict]) -> dict[str, dict]:
    """Aggregate reviews into monthly buckets for the last 12 months.

    Returns:
        {
          "YYYY-MM": {"count": N, "avg_rating": X.X},
          ...
        }

    Only the trailing 12 months are included — older reviews don't improve
    year-over-year accuracy but would inflate the monthly_breakdown payload.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=366)

    monthly_counts: dict[str, int] = defaultdict(int)
    # dict[str, list[float]] accumulates ratings before averaging
    monthly_ratings: dict[str, list] = defaultdict(list)

    for review in reviews:
        dt = _parse_review_timestamp(review)
        if dt is None or dt < cutoff:
            continue

        month_key = dt.strftime("%Y-%m")
        monthly_counts[month_key] += 1

        rating = review.get("review_rating")
        if isinstance(rating, (int, float)):
            monthly_ratings[month_key].append(float(rating))

    result = {}
    for month in sorted(monthly_counts.keys()):
        ratings = monthly_ratings[month]
        result[month] = {
            "count": monthly_counts[month],
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        }
    return result


def _parse_review_timestamp(review: dict):
    """Return a timezone-aware datetime from a review dict, or None on failure."""
    ts = review.get("review_timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            pass

    # Fall back to the UTC datetime string Outscraper sometimes includes.
    # Observed format: "05/06/2024 20:13:20" (MM/DD/YYYY HH:MM:SS)
    dt_str = review.get("review_datetime_utc", "")
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None
