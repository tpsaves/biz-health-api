import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_FIELDS = "name,rating,user_ratings_total,reviews,opening_hours"


def _review_dt(review: dict) -> datetime | None:
    """Parse a review timestamp from either API format.

    The legacy Places API returns "time" as a Unix integer (seconds since
    epoch).  The newer Places API v1 returns "publishTime" as an RFC 3339
    string.  We prefer publishTime when present so the scraper works with
    both API versions without changes to the caller.

    C# equivalents:
      publishTime → DateTime.Parse(s, null, DateTimeStyles.RoundtripKind)
      time        → DateTimeOffset.FromUnixTimeSeconds(ts).UtcDateTime
    Python notes:
      datetime.fromisoformat() handles "Z" suffix only in Python 3.11+;
      replace("Z", "+00:00") makes it safe on 3.9/3.10 too.
      datetime.fromtimestamp(ts, tz=...) is always UTC when tz=timezone.utc —
      unlike C# DateTime.FromFileTimeUtc which uses a different epoch (1601).
    """
    if pt := review.get("publishTime"):
        # ISO 8601 / RFC 3339 string, e.g. "2026-03-15T10:30:00Z"
        return datetime.fromisoformat(pt.replace("Z", "+00:00"))
    ts = review.get("time")
    if ts:
        # Unix seconds since 1970-01-01 UTC → timezone-aware datetime.
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return None


def _extract_velocity_metrics(result: dict) -> dict:
    """Derive velocity metrics from the reviews list in a Places API result.

    Google Places returns at most 5 reviews, so monthly_from_reviews and
    days_since_last_review are based on a small sample.  The scoring engine
    treats high total review counts as the primary volume signal and uses these
    metrics only for recency and trend direction.
    """
    now_utc = datetime.now(timezone.utc)
    reviews  = result.get("reviews") or []

    # Sort ascending so index -1 is the newest.
    # C# equivalent: reviews.OrderBy(r => r.Time)
    reviews_sorted = sorted(reviews, key=lambda r: _review_dt(r) or datetime.min.replace(tzinfo=timezone.utc))

    days_since_last_review: int | None = None
    if reviews_sorted:
        latest_dt = _review_dt(reviews_sorted[-1])
        if latest_dt:
            # timedelta.days truncates to whole days — same as (int)(span.TotalDays) in C#.
            days_since_last_review = (now_utc - latest_dt).days

    # Compute per-window averages and 1-star rates.
    # _review_dt() handles both "time" and "publishTime" fields transparently.
    def _window(days: int) -> list[dict]:
        cutoff = now_utc.timestamp() - days * 86400
        return [r for r in reviews if (_review_dt(r) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() >= cutoff]

    last_60  = _window(60)
    prior_60 = [r for r in reviews
                if (_review_dt(r) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() < now_utc.timestamp() - 60  * 86400
                and (_review_dt(r) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() >= now_utc.timestamp() - 120 * 86400]

    def _avg_rating(rs: list[dict]) -> float | None:
        ratings = [r.get("rating") for r in rs if r.get("rating") is not None]
        return round(sum(ratings) / len(ratings), 2) if ratings else None

    avg_last_60  = _avg_rating(last_60)
    avg_prior_60 = _avg_rating(prior_60)

    one_star_60d      = sum(1 for r in last_60  if r.get("rating") == 1)
    one_star_lifetime = sum(1 for r in reviews  if r.get("rating") == 1)

    one_star_pct_60d      = round(one_star_60d  / len(last_60)  * 100) if last_60  else None
    one_star_pct_lifetime = round(one_star_lifetime / len(reviews) * 100) if reviews else None

    # Owner response rate: reviews where author_url exists for owner_response.
    # Google Places API includes owner_response inside each review dict when present.
    responses_90d = sum(
        1 for r in _window(90)
        if r.get("owner_response") or r.get("response")
    )
    total_90d = len(_window(90))
    owner_response_rate = round(responses_90d / total_90d * 100) if total_90d else 0

    # Monthly review buckets from timestamps — keyed as "YYYY-MM".
    # Works for both "time" (Unix int) and "publishTime" (ISO string) via _review_dt().
    monthly_from_reviews: dict[str, int] = defaultdict(int)
    for r in reviews:
        dt = _review_dt(r)
        if dt:
            monthly_from_reviews[f"{dt.year}-{dt.month:02d}"] += 1

    return {
        "days_since_last_review":  days_since_last_review,
        "avg_rating_last_60d":     avg_last_60,
        "avg_rating_prior_60d":    avg_prior_60,
        "one_star_pct_60d":        one_star_pct_60d,
        "one_star_pct_lifetime":   one_star_pct_lifetime,
        "owner_response_rate":     owner_response_rate,
        "monthly_from_reviews":    dict(monthly_from_reviews),  # {"YYYY-MM": count}
        "reviews_in_last_60d":     len(last_60),
        "reviews_in_prior_60d":    len(prior_60),
    }


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

    # Augment payload with velocity metrics derived from review timestamps.
    result = payload.get("result", {})
    payload["velocity_metrics"] = _extract_velocity_metrics(result)

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

    logger.info(
        "Stored Google Places signal — place_id=%s rating=%s reviews=%s days_since_last=%s",
        place_id,
        result.get("rating"),
        result.get("user_ratings_total"),
        payload["velocity_metrics"].get("days_since_last_review"),
    )
    return payload
