import json
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_REVIEW_COUNT = 2000

_HEALTH_WEIGHT = 0.50
_TABC_WEIGHT   = 0.30
_HOURS_WEIGHT  = 0.20

_VELOCITY_WEIGHT    = 0.25
_RATING_WEIGHT      = 0.25
_OPERATIONAL_WEIGHT = 0.50

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

    Returns a dict with all scores, operational sub-scores, and score_factors.
    """

    def _latest(source: str):
        # Select both payload and scraped_at so factor generation has dates.
        return session.execute(
            text(
                """
                SELECT payload, scraped_at
                FROM raw_signals
                WHERE restaurant_id = :rid AND source = :src
                ORDER BY scraped_at DESC
                LIMIT 1
                """
            ),
            {"rid": restaurant_id, "src": source},
        ).one_or_none()

    google_row     = _latest("google_places")
    foursquare_row = _latest("foursquare")
    inspection_row = _latest("health_inspections")
    tabc_row       = _latest("tabc_license")
    hours_row      = _latest("hours_monitor")

    # ── review_velocity_score ──────────────────────────────────────────────────
    google_review_count = 0
    google_rating: Optional[float] = None
    google_scraped_date: Optional[str] = None

    if google_row:
        result = google_row.payload.get("result", {})
        google_review_count = result.get("user_ratings_total") or 0
        google_rating = result.get("rating")
        if google_row.scraped_at:
            google_scraped_date = google_row.scraped_at.strftime("%Y-%m-%d")

    review_velocity_score = min(100, int(google_review_count / _MAX_REVIEW_COUNT * 100))

    # ── rating_trend_score ─────────────────────────────────────────────────────
    foursquare_rating: Optional[float] = None
    fsq_scraped_date: Optional[str] = None
    if foursquare_row:
        foursquare_rating = foursquare_row.payload.get("details", {}).get("rating")
        if foursquare_row.scraped_at:
            fsq_scraped_date = foursquare_row.scraped_at.strftime("%Y-%m-%d")

    normalized: list[float] = []
    if google_rating is not None:
        normalized.append(_normalize_google_rating(google_rating))
    if foursquare_rating is not None:
        normalized.append(_normalize_foursquare_rating(foursquare_rating))

    rating_trend_score = int(sum(normalized) / len(normalized)) if normalized else 0

    # ── operational sub-components ─────────────────────────────────────────────
    health_component: int = 0
    inspection_records_raw: list = []
    insp_scraped_date: Optional[str] = None

    if inspection_row:
        inspection_records_raw = inspection_row.payload.get("records", [])
        if inspection_row.scraped_at:
            insp_scraped_date = inspection_row.scraped_at.strftime("%Y-%m-%d")
        if inspection_records_raw:
            health_component = int(float(inspection_records_raw[0].get("score", 0)))

    tabc_component: int = 0
    tabc_record: Optional[dict] = None
    tabc_scraped_date: Optional[str] = None

    if tabc_row:
        tabc_records = tabc_row.payload.get("records", [])
        if tabc_row.scraped_at:
            tabc_scraped_date = tabc_row.scraped_at.strftime("%Y-%m-%d")
        if tabc_records:
            tabc_record = tabc_records[0]
            license_type = tabc_record.get("aimslicensetype", "")
            tabc_component = _LICENSE_TYPE_SCORES.get(license_type, _DEFAULT_LICENSE_SCORE)

    hours_component: int = 0
    hours_payload: Optional[dict] = None
    hours_scraped_date: Optional[str] = None

    if hours_row:
        hours_payload = hours_row.payload
        if hours_row.scraped_at:
            hours_scraped_date = hours_row.scraped_at.strftime("%Y-%m-%d")
        hours_component = hours_payload.get("hours_completeness", 0)

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

    # ── score_factors ──────────────────────────────────────────────────────────
    # Each factor: signal, label, value, date, impact (positive/neutral/negative), weight (high/medium/low)

    review_velocity_factors: list[dict] = []
    if google_review_count > 0:
        review_velocity_factors.append({
            "signal": "google_review_count",
            "label": "Google review volume",
            "value": f"{google_review_count:,} reviews",
            "date": google_scraped_date or "",
            "impact": "positive" if google_review_count >= 1000 else ("neutral" if google_review_count >= 300 else "negative"),
            "weight": "high",
        })
    else:
        review_velocity_factors.append({
            "signal": "google_review_count",
            "label": "Google review volume",
            "value": "No data",
            "date": "",
            "impact": "negative",
            "weight": "high",
        })

    rating_trend_factors: list[dict] = []
    if google_rating is not None:
        rating_trend_factors.append({
            "signal": "google_rating",
            "label": "Google rating",
            "value": f"{google_rating:.1f} / 5.0",
            "date": google_scraped_date or "",
            "impact": "positive" if google_rating >= 4.2 else ("neutral" if google_rating >= 3.8 else "negative"),
            "weight": "high",
        })
    if foursquare_rating is not None:
        rating_trend_factors.append({
            "signal": "foursquare_rating",
            "label": "Foursquare rating",
            "value": f"{foursquare_rating:.1f} / 10.0",
            "date": fsq_scraped_date or "",
            "impact": "positive" if foursquare_rating >= 8.0 else ("neutral" if foursquare_rating >= 7.0 else "negative"),
            "weight": "medium",
        })

    operational_factors: list[dict] = []

    if inspection_records_raw:
        latest_insp = inspection_records_raw[0]
        insp_score = int(float(latest_insp.get("score", 0)))
        insp_date = (latest_insp.get("insp_date") or "")[:10]

        operational_factors.append({
            "signal": "health_inspection",
            "label": "Latest inspection score",
            "value": f"{insp_score}/100",
            "date": insp_date,
            "impact": "positive" if insp_score >= 90 else ("neutral" if insp_score >= 80 else "negative"),
            "weight": "high",
        })

        if len(inspection_records_raw) >= 2:
            prev_score = int(float(inspection_records_raw[1].get("score", 0)))
            if insp_score > prev_score + 3:
                trend, trend_impact = "Improving", "positive"
            elif insp_score < prev_score - 3:
                trend, trend_impact = "Declining", "negative"
            else:
                trend, trend_impact = "Stable", "neutral"

            operational_factors.append({
                "signal": "inspection_trend",
                "label": "Inspection trend",
                "value": trend,
                "date": insp_date,
                "impact": trend_impact,
                "weight": "medium",
            })

    if tabc_record:
        license_type = tabc_record.get("aimslicensetype", "")
        ls = _LICENSE_TYPE_SCORES.get(license_type, _DEFAULT_LICENSE_SCORE)
        operational_factors.append({
            "signal": "tabc_license",
            "label": "TABC license type",
            "value": license_type,
            "date": tabc_scraped_date or "",
            "impact": "positive" if ls >= 85 else "neutral",
            "weight": "high",
        })
        operational_factors.append({
            "signal": "tabc_license_status",
            "label": "TABC license status",
            "value": "Active",
            "date": tabc_scraped_date or "",
            "impact": "positive",
            "weight": "high",
        })
    else:
        operational_factors.append({
            "signal": "tabc_license_status",
            "label": "TABC license status",
            "value": "No record found",
            "date": "",
            "impact": "negative",
            "weight": "high",
        })

    if hours_payload is not None:
        days_with_hours = hours_payload.get("days_with_hours", 0)
        completeness = hours_payload.get("hours_completeness", 0)
        operational_factors.append({
            "signal": "hours_completeness",
            "label": "Hours on file",
            "value": f"{days_with_hours}/7 days",
            "date": hours_scraped_date or "",
            "impact": "positive" if completeness >= 100 else ("neutral" if completeness >= 70 else "negative"),
            "weight": "medium",
        })

    score_factors = {
        "reviewVelocity": review_velocity_factors,
        "ratingTrend":    rating_trend_factors,
        "operational":    operational_factors,
    }

    # ── Write to health_scores ─────────────────────────────────────────────────
    session.execute(
        text(
            """
            INSERT INTO health_scores
                (restaurant_id, review_velocity_score, rating_trend_score,
                 operational_score, overall_score, score_factors)
            VALUES
                (:restaurant_id, :review_velocity_score, :rating_trend_score,
                 :operational_score, :overall_score, CAST(:score_factors AS jsonb))
            """
        ),
        {
            "restaurant_id":         restaurant_id,
            "review_velocity_score": review_velocity_score,
            "rating_trend_score":    rating_trend_score,
            "operational_score":     operational_score,
            "overall_score":         overall_score,
            "score_factors":         json.dumps(score_factors),
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
        "staffing_score": None,
        "overall_score":  overall_score,
        "score_factors":  score_factors,
    }
    logger.info("v2 scored restaurant_id=%s overall=%s", restaurant_id, overall_score)
    return scores
