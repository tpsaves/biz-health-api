import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Ceiling for review count normalization.
# A restaurant with 2000+ reviews is extremely well-established; score is capped at 100.
_MAX_REVIEW_COUNT = 2000


def _normalize_google_rating(rating: float) -> float:
    """Convert Google's 1.0–5.0 rating to a 0–100 scale."""
    # Google never goes below 1.0 in practice, so the effective range is 4.0 points.
    return (rating - 1.0) / 4.0 * 100.0


def _normalize_foursquare_rating(rating: float) -> float:
    """Convert Foursquare's 0–10 rating to a 0–100 scale."""
    return rating / 10.0 * 100.0


def compute_scores(restaurant_id: str, session: Session) -> dict:
    """Read the latest raw_signals for a restaurant and write a row to health_scores.

    Returns the computed scores as a plain dict so callers can print or assert on them.

    In C# you'd return a typed DTO; here we use a plain dict because Python dicts are
    the idiomatic lightweight data container (no class boilerplate required).
    """
    # ── Pull the most recent Google Places signal ──────────────────────────────
    google_row = session.execute(
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
    ).one_or_none()  # one_or_none() returns None if no row; equivalent to FirstOrDefault() in LINQ

    # ── Pull the most recent Foursquare signal ─────────────────────────────────
    foursquare_row = session.execute(
        text(
            """
            SELECT payload
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'foursquare'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    # ── Extract raw values from JSONB payloads ─────────────────────────────────
    # SQLAlchemy returns JSONB columns as native Python dicts — no deserialization needed.
    google_review_count: int = 0
    google_rating: Optional[float] = None

    if google_row is not None:
        result = google_row.payload.get("result", {})
        google_review_count = result.get("user_ratings_total") or 0
        google_rating = result.get("rating")  # float 1.0–5.0, or None if absent

    foursquare_rating: Optional[float] = None

    if foursquare_row is not None:
        details = foursquare_row.payload.get("details", {})
        foursquare_rating = details.get("rating")  # float 0–10, or None if venue has too few ratings

    # ── review_velocity_score (40% weight) ────────────────────────────────────
    # Proxy for review momentum: normalize total Google review count to 0–100.
    # True velocity would require a time series; this is the best single-snapshot signal.
    review_velocity_score = min(100, int(google_review_count / _MAX_REVIEW_COUNT * 100))

    # ── rating_trend_score (60% weight) ───────────────────────────────────────
    # Average of all available normalized ratings (Google + Foursquare).
    # Using a list so the average adapts automatically when sources are missing.
    normalized_ratings: list[float] = []

    if google_rating is not None:
        normalized_ratings.append(_normalize_google_rating(google_rating))

    if foursquare_rating is not None:
        normalized_ratings.append(_normalize_foursquare_rating(foursquare_rating))

    if normalized_ratings:
        # sum/len is the Python idiom for a simple mean — no statistics module needed
        rating_trend_score = int(sum(normalized_ratings) / len(normalized_ratings))
    else:
        rating_trend_score = 0
        logger.warning("No rating data available for restaurant_id=%s", restaurant_id)

    # ── overall_score ──────────────────────────────────────────────────────────
    overall_score = int(review_velocity_score * 0.4 + rating_trend_score * 0.6)

    # ── Write to health_scores ─────────────────────────────────────────────────
    session.execute(
        text(
            """
            INSERT INTO health_scores (restaurant_id, review_velocity_score, rating_trend_score, overall_score)
            VALUES (:restaurant_id, :review_velocity_score, :rating_trend_score, :overall_score)
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "review_velocity_score": review_velocity_score,
            "rating_trend_score": rating_trend_score,
            "overall_score": overall_score,
        },
    )
    session.commit()

    scores = {
        "review_velocity_score": review_velocity_score,
        "rating_trend_score": rating_trend_score,
        "overall_score": overall_score,
    }
    logger.info("Scored restaurant_id=%s scores=%s", restaurant_id, scores)
    return scores
