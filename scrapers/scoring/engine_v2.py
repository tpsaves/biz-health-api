import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_REVIEW_COUNT = 2000

# operational_score sub-weights (must sum to 1.0)
_HEALTH_WEIGHT = 0.50
_TABC_WEIGHT   = 0.30
_HOURS_WEIGHT  = 0.20

# overall_score weights (staffing excluded until Phase 3; these sum to 1.0)
_VELOCITY_WEIGHT    = 0.25
_RATING_WEIGHT      = 0.25
_OPERATIONAL_WEIGHT = 0.50

# Score awarded per TABC license type — higher-tier permits = stronger signal
_LICENSE_TYPE_SCORES: dict[str, int] = {
    "MB": 100,
    "BG": 85,
    "BQ": 75,
    "P":  70,
}
_DEFAULT_LICENSE_SCORE = 65


def _normalize_google_rating(rating: float) -> float:
    return (rating - 1.0) / 4.0 * 100.0


def _normalize_foursquare_rating(rating: float) -> float:
    return rating / 10.0 * 100.0


def compute_scores_v2(restaurant_id: str, session: Session) -> dict:
    """Read all available raw_signals and write a full score row to health_scores.

    Computes four component scores. staffing_score is left NULL until Phase 3.
    Returns a dict with all scores and operational sub-scores for caller display.
    """

    def _latest(source: str):
        """Fetch the most recent raw_signal payload for a given source, or None."""
        # Assigning _latest as a closure over session avoids repeating the same
        # boilerplate query six times — equivalent to a local helper method in C#.
        return session.execute(
            text(
                """
                SELECT payload
                FROM raw_signals
                WHERE restaurant_id = :rid AND source = :src
                ORDER BY scraped_at DESC
                LIMIT 1
                """
            ),
            {"rid": restaurant_id, "src": source},
        ).one_or_none()

    google_row       = _latest("google_places")
    foursquare_row   = _latest("foursquare")
    inspection_row   = _latest("health_inspections")
    tabc_row         = _latest("tabc_license")
    hours_row        = _latest("hours_monitor")

    # ── review_velocity_score ──────────────────────────────────────────────────
    google_review_count = 0
    google_rating: Optional[float] = None

    if google_row:
        result = google_row.payload.get("result", {})
        google_review_count = result.get("user_ratings_total") or 0
        google_rating = result.get("rating")

    review_velocity_score = min(100, int(google_review_count / _MAX_REVIEW_COUNT * 100))

    # ── rating_trend_score ─────────────────────────────────────────────────────
    foursquare_rating: Optional[float] = None
    if foursquare_row:
        foursquare_rating = foursquare_row.payload.get("details", {}).get("rating")

    normalized: list[float] = []
    if google_rating is not None:
        normalized.append(_normalize_google_rating(google_rating))
    if foursquare_rating is not None:
        normalized.append(_normalize_foursquare_rating(foursquare_rating))

    rating_trend_score = int(sum(normalized) / len(normalized)) if normalized else 0

    # ── operational sub-components ─────────────────────────────────────────────

    # Health inspection: latest score is already on a 0–100 scale
    health_component: int = 0
    if inspection_row:
        records = inspection_row.payload.get("records", [])
        if records:
            # score is returned as a string from Socrata — int() converts it
            health_component = int(float(records[0].get("score", 0)))

    # TABC license: score by license type; 0 if no record found
    tabc_component: int = 0
    if tabc_row:
        records = tabc_row.payload.get("records", [])
        if records:
            license_type = records[0].get("aimslicensetype", "")
            tabc_component = _LICENSE_TYPE_SCORES.get(license_type, _DEFAULT_LICENSE_SCORE)

    # Hours completeness: pre-computed by hours_monitor scraper
    hours_component: int = 0
    if hours_row:
        hours_component = hours_row.payload.get("hours_completeness", 0)

    operational_score = int(
        health_component * _HEALTH_WEIGHT
        + tabc_component  * _TABC_WEIGHT
        + hours_component * _HOURS_WEIGHT
    )

    # ── overall_score ──────────────────────────────────────────────────────────
    overall_score = int(
        review_velocity_score * _VELOCITY_WEIGHT
        + rating_trend_score  * _RATING_WEIGHT
        + operational_score   * _OPERATIONAL_WEIGHT
    )

    # ── Write to health_scores ─────────────────────────────────────────────────
    session.execute(
        text(
            """
            INSERT INTO health_scores
                (restaurant_id, review_velocity_score, rating_trend_score,
                 operational_score, overall_score)
            VALUES
                (:restaurant_id, :review_velocity_score, :rating_trend_score,
                 :operational_score, :overall_score)
            """
        ),
        {
            "restaurant_id":         restaurant_id,
            "review_velocity_score": review_velocity_score,
            "rating_trend_score":    rating_trend_score,
            "operational_score":     operational_score,
            "overall_score":         overall_score,
        },
    )
    session.commit()

    scores = {
        "review_velocity_score": review_velocity_score,
        "rating_trend_score":    rating_trend_score,
        "operational_score":     operational_score,
        "operational_components": {
            "health_inspection":  health_component,
            "tabc_license":       tabc_component,
            "hours_completeness": hours_component,
        },
        "staffing_score":  None,
        "overall_score":   overall_score,
    }
    logger.info("v2 scored restaurant_id=%s scores=%s", restaurant_id, scores)
    return scores
