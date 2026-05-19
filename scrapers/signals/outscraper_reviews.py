"""Outscraper Google Maps Reviews scraper.

Fetches up to 12 months of review history per restaurant, enabling true monthly
breakdowns that the Google Places API cannot provide (it returns only 5 reviews max).
Outscraper is pay-per-use, so this runs biweekly (1st and 15th of each month).
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

from signals.outscraper_quota import check_quota, log_usage, MONTHLY_CAP

logger = logging.getLogger(__name__)

_OUTSCRAPER_URL = "https://api.app.outscraper.com/maps/reviews-v3"
_POLL_URL = "https://api.app.outscraper.com/requests/{}"
# 46 reviews × 107 restaurants × 2 runs/month = 9,844 records (~$29.53/month)
_MAX_REVIEWS = 46
_POLL_RETRIES = 20
_POLL_INTERVAL = 6   # seconds between status polls


def scrape_outscraper_reviews(
    google_place_id: str,
    name: str,
    restaurant_id: str,
    session: Session,
    city: str = "",
) -> dict:
    """Fetch up to 12 months of Google reviews from Outscraper and write to raw_signals.

    Outscraper returns full review history with timestamps, enabling true monthly
    breakdowns for year-over-year comparison — something the 5-review Google
    Places API snapshot cannot support.

    Uses a text search query (name + city) rather than place_id: prefix because
    the place_id: format returns empty results from the reviews-v3 endpoint.
    Text search is disambiguated by including the city when available.

    In C#, you'd model the polling loop as a Task with CancellationToken;
    here we block the calling thread with time.sleep() since APScheduler runs
    jobs in daemon threads and blocking is acceptable for a background job.
    """
    api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OUTSCRAPER_API_KEY not set in environment")

    # Quota check — enforce monthly cap before hitting the API.
    quota = check_quota(_MAX_REVIEWS, session)
    if quota["remaining"] == 0:
        logger.warning(
            "[outscraper] monthly cap reached (%d records) — skipping %s",
            MONTHLY_CAP, name,
        )
        session.execute(
            text(
                """
                INSERT INTO raw_signals (restaurant_id, source, payload)
                VALUES (:rid, 'outscraper_quota_exceeded',
                        CAST(:payload AS jsonb))
                """
            ),
            {
                "rid":     restaurant_id,
                "payload": json.dumps({"month": time.strftime("%Y-%m"), "cap": MONTHLY_CAP}),
            },
        )
        session.commit()
        return {"monthly_breakdown": {}, "total_reviews_fetched": 0, "quota_exceeded": True}

    reviews_limit = _MAX_REVIEWS if quota["remaining"] >= _MAX_REVIEWS else quota["remaining"]
    if reviews_limit < _MAX_REVIEWS:
        logger.info(
            "[outscraper] reduced reviewsLimit to %d (only %d records remaining in quota)",
            reviews_limit, quota["remaining"],
        )

    headers = {"X-API-KEY": api_key}

    # Text search disambiguated with city avoids matching wrong locations for chains.
    # The place_id: prefix format returns empty results on the reviews-v3 endpoint.
    query = f"{name} {city}".strip() if city else name

    resp = httpx.get(
        _OUTSCRAPER_URL,
        params={
            "query": query,
            "limit": 1,             # number of places to look up
            "reviewsLimit": reviews_limit,
            "sort": "newest",
        },
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code == 402:
        logger.info("[outscraper] account has no credits — skipping %s", name)
        return {"monthly_breakdown": {}, "total_reviews_fetched": 0}
    resp.raise_for_status()
    raw = resp.json()

    # Outscraper always returns async (status=Pending) for review requests.
    # Poll until the task completes — typically 10-30 seconds.
    if raw.get("status") in ("Pending", None) or not raw.get("data"):
        raw = _poll_for_results(raw.get("id", ""), headers)

    reviews = _extract_reviews(raw)
    monthly_breakdown = _build_monthly_breakdown(reviews)

    payload = {
        "monthly_breakdown": monthly_breakdown,
        "total_reviews_fetched": len(reviews),
        "google_place_id": google_place_id,
        "name": name,
        "query": query,
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

    log_usage(restaurant_id, len(reviews), session)

    logger.info(
        "Stored outscraper_reviews signal — name=%s months=%d total_reviews=%d",
        name,
        len(monthly_breakdown),
        len(reviews),
    )
    return payload


def _poll_for_results(request_id: str, headers: dict) -> dict:
    """Poll Outscraper until the async task completes."""
    if not request_id:
        raise RuntimeError("No request ID to poll")

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
    """Pull the flat list of review objects out of the Outscraper response envelope.

    The reviews-v3 endpoint returns a list of place objects, each with a
    'reviews_data' key containing the individual review records.
    """
    data = raw.get("data", [])
    if not data:
        return []

    first = data[0]

    # Current format: data = [place_object] where place_object has reviews_data list.
    if isinstance(first, dict):
        return first.get("reviews_data", [])

    # Legacy format: data = [[review, review, ...]] (flat list of review dicts).
    if isinstance(first, list):
        return first

    return []


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

    # Observed format from Outscraper: "05/12/2026 17:40:48" (MM/DD/YYYY HH:MM:SS)
    dt_str = review.get("review_datetime_utc", "")
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None
