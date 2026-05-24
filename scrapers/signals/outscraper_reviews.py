"""Outscraper Google Maps Reviews scraper.

Fetches recent reviews per restaurant and stores the complete raw review objects.
The scraper's only job is to fetch faithfully and store everything — aggregates
(monthly counts, rating distribution, keyword findings) are computed by the
scoring engine at score time from the stored reviews_data.
Runs biweekly on the 1st and 15th of each month.
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

from signals.outscraper_quota import check_quota, log_usage, log_backfill_usage, MONTHLY_CAP

logger = logging.getLogger(__name__)

_OUTSCRAPER_URL = "https://api.app.outscraper.com/maps/reviews-v3"
_POLL_URL       = "https://api.app.outscraper.com/requests/{}"
# 70 reviews × 107 restaurants × 2 runs/month = 14,980 records (~$44.94/month)
_MAX_REVIEWS   = 70
_POLL_RETRIES  = 20
_POLL_INTERVAL = 6   # seconds between status polls


def scrape_outscraper_reviews(
    google_place_id: str,
    name: str,
    restaurant_id: str,
    session: Session,
    city: str = "",
    max_reviews: int | None = None,
    is_backfill: bool = False,
) -> dict:
    """Fetch recent Google reviews from Outscraper and store the full raw payload.

    Stores the complete reviews_data array so the scoring engine can compute
    any aggregate (rating distribution, keyword flags, response rates) at score
    time without re-fetching. monthly_breakdown is also stored as a convenience
    for the sparkline chart which needs 12-month counts without re-parsing.

    Uses a text search query (name + city) rather than place_id: prefix because
    the place_id: format returns empty results from the reviews-v3 endpoint.

    max_reviews overrides _MAX_REVIEWS (used by the backfill job: 200 reviews).
    is_backfill=True bypasses the monthly cap check and logs usage separately.
    """
    api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OUTSCRAPER_API_KEY not set in environment")

    effective_max = max_reviews if max_reviews is not None else _MAX_REVIEWS

    if is_backfill:
        reviews_limit = effective_max
    else:
        # Quota check — enforce monthly cap before hitting the API.
        quota = check_quota(effective_max, session)
        if quota["remaining"] == 0:
            logger.warning(
                "[outscraper] monthly cap reached (%d records) — skipping %s",
                MONTHLY_CAP, name,
            )
            session.execute(
                text(
                    "INSERT INTO raw_signals (restaurant_id, source, payload) "
                    "VALUES (:rid, 'outscraper_quota_exceeded', CAST(:payload AS jsonb))"
                ),
                {
                    "rid":     restaurant_id,
                    "payload": json.dumps({"month": time.strftime("%Y-%m"), "cap": MONTHLY_CAP}),
                },
            )
            session.commit()
            return {"reviews_data": [], "monthly_breakdown": {}, "total_reviews_fetched": 0, "quota_exceeded": True}

        reviews_limit = effective_max if quota["remaining"] >= effective_max else quota["remaining"]
        if reviews_limit < effective_max:
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
            "query":        query,
            "limit":        1,             # number of places to look up
            "reviewsLimit": reviews_limit,
            "sort":         "newest",
        },
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code == 402:
        logger.info("[outscraper] account has no credits — skipping %s", name)
        return {"reviews_data": [], "monthly_breakdown": {}, "total_reviews_fetched": 0}
    resp.raise_for_status()
    raw = resp.json()

    # Outscraper returns async (status=Pending) for review requests.
    # Poll until complete — typically 10-30 seconds.
    if raw.get("status") in ("Pending", None) or not raw.get("data"):
        raw = _poll_for_results(raw.get("id", ""), headers)

    reviews          = _extract_reviews(raw)
    monthly_breakdown = _build_monthly_breakdown(reviews)

    payload = {
        "reviews_data":          reviews,           # full raw review objects — source of truth
        "monthly_breakdown":     monthly_breakdown, # convenience aggregate for sparkline chart
        "total_reviews_fetched": len(reviews),
        "google_place_id":       google_place_id,
        "name":                  name,
        "query":                 query,
    }

    session.execute(
        text(
            "INSERT INTO raw_signals (restaurant_id, source, payload) "
            "VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))"
        ),
        {
            "restaurant_id": restaurant_id,
            "source":        "outscraper_reviews",
            "payload":       json.dumps(payload),
        },
    )
    session.commit()

    if is_backfill:
        log_backfill_usage(restaurant_id, len(reviews), session)
    else:
        log_usage(restaurant_id, len(reviews), session)

    logger.info(
        "Stored outscraper_reviews signal — name=%s months=%d total_reviews=%d",
        name, len(monthly_breakdown), len(reviews),
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
    if isinstance(first, dict):
        return first.get("reviews_data", [])
    if isinstance(first, list):
        return first
    return []


def _build_monthly_breakdown(reviews: list[dict]) -> dict[str, dict]:
    """Aggregate reviews into monthly buckets for the last 12 months.

    Stored alongside reviews_data as a convenience for the sparkline chart.
    The scoring engine reads this directly rather than re-aggregating on every
    score computation.

    Returns:
        {"YYYY-MM": {"count": N, "avg_rating": X.X}, ...}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=366)
    monthly_counts:  dict[str, int]   = defaultdict(int)
    monthly_ratings: dict[str, list]  = defaultdict(list)

    for review in reviews:
        dt = _parse_review_timestamp(review)
        if dt is None or dt < cutoff:
            continue
        month_key = dt.strftime("%Y-%m")
        monthly_counts[month_key] += 1
        rating = review.get("review_rating")
        if isinstance(rating, (int, float)):
            monthly_ratings[month_key].append(float(rating))

    return {
        month: {
            "count":      monthly_counts[month],
            "avg_rating": round(sum(monthly_ratings[month]) / len(monthly_ratings[month]), 2)
                          if monthly_ratings[month] else None,
        }
        for month in sorted(monthly_counts.keys())
    }


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
