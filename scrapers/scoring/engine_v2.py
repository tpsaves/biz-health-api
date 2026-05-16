import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from scoring import seasonality

logger = logging.getLogger(__name__)

_MAX_REVIEW_COUNT = 2000

_HEALTH_WEIGHT = 0.50
_TABC_WEIGHT   = 0.30
_HOURS_WEIGHT  = 0.20

# Phase 8: weights rebalanced to incorporate financial_risk_score.
_VELOCITY_WEIGHT    = 0.20
_RATING_WEIGHT      = 0.30
_OPERATIONAL_WEIGHT = 0.30
_FINANCIAL_WEIGHT   = 0.20

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


def _parse_monthly_counts(monthly_from_reviews: dict[str, int]) -> dict[tuple[int, int], int]:
    """Convert {"YYYY-MM": count} → {(year, month): count} for seasonality functions."""
    result = {}
    for key, count in monthly_from_reviews.items():
        try:
            year, month = key.split("-")
            result[(int(year), int(month))] = count
        except ValueError:
            pass
    return result


def compute_scores_v2(restaurant_id: str, session: Session) -> dict:
    """Read all available raw_signals and write a full score row to health_scores."""

    def _latest(source: str):
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

    google_row       = _latest("google_places")
    foursquare_row   = _latest("foursquare")
    inspection_row   = _latest("health_inspections")
    tabc_row         = _latest("tabc_license")
    hours_row        = _latest("hours_monitor")
    outscraper_row   = _latest("outscraper_reviews")
    sba_row          = _latest("sba_loans")
    prop_tax_row     = _latest("property_tax")

    # ── Base review data ───────────────────────────────────────────────────────
    google_review_count = 0
    google_rating: Optional[float] = None
    google_scraped_date: Optional[str] = None
    velocity_metrics: dict = {}

    if google_row:
        result = google_row.payload.get("result", {})
        google_review_count = result.get("user_ratings_total") or 0
        google_rating = result.get("rating")
        if google_row.scraped_at:
            google_scraped_date = google_row.scraped_at.strftime("%Y-%m-%d")
        velocity_metrics = google_row.payload.get("velocity_metrics", {})

    # ── review_velocity_score ──────────────────────────────────────────────────
    review_velocity_score = min(100, int(google_review_count / _MAX_REVIEW_COUNT * 100))

    # Phase 6b enhanced velocity flags and adjustments
    review_gap_alert   = False
    one_star_spike     = False
    # days_since_last_review is pre-computed by google_places.scrape_place()
    # using _review_dt(), which handles both legacy "time" (Unix int) and
    # the new Places API v1 "publishTime" (ISO 8601 string).
    # C# equivalent: DateTime.Parse(publishTime, ...) vs DateTimeOffset.FromUnixTimeSeconds(time)
    days_since_last    = velocity_metrics.get("days_since_last_review")
    owner_resp_rate    = velocity_metrics.get("owner_response_rate", 0)
    one_star_pct_60d   = velocity_metrics.get("one_star_pct_60d")
    monthly_from_rev   = velocity_metrics.get("monthly_from_reviews", {})
    scrape_date_str    = google_scraped_date

    # Determine comparison method and monthly_volume_trend via seasonality module.
    # Use the scrape date's year/month as the "current" reference point.
    monthly_volume_trend   = "insufficient_data"
    seasonality_adjusted   = False
    comparison_method      = "insufficient_data"

    # Prefer Outscraper monthly_breakdown when available — it contains real review
    # timestamps for up to 12 months, enabling year_over_year comparison.
    # Fall back to the small sample accumulated from daily Google Places snapshots.
    outscraper_monthly: dict = {}
    if outscraper_row:
        outscraper_monthly = outscraper_row.payload.get("monthly_breakdown", {})

    # Refine days_since_last using Outscraper's most recent month with reviews.
    # Google Places only sees the 5 most recent reviews, so its days_since_last
    # is stale when Outscraper has more recent monthly data.
    # Use the first day of the most recent non-zero month as a conservative bound.
    if outscraper_monthly:
        months_with_reviews = [
            k for k, v in outscraper_monthly.items()
            if isinstance(v, dict) and v.get("count", 0) > 0
        ]
        if months_with_reviews:
            yr, mo   = map(int, max(months_with_reviews).split("-"))
            month_start = datetime(yr, mo, 1, tzinfo=timezone.utc)
            or_days  = (datetime.now(timezone.utc) - month_start).days
            if days_since_last is None or or_days < days_since_last:
                days_since_last = or_days

    if outscraper_monthly:
        # Convert {"YYYY-MM": {"count": N, ...}} → {(year, month): count}
        # Same tuple-keyed format that seasonality.year_over_year() expects.
        monthly_counts_by_ym = {}
        for key, val in outscraper_monthly.items():
            try:
                year, month = key.split("-")
                monthly_counts_by_ym[(int(year), int(month))] = val["count"]
            except (ValueError, KeyError):
                pass
    else:
        monthly_counts_by_ym = _parse_monthly_counts(monthly_from_rev)

    if monthly_counts_by_ym:
        try:
            scrape_dt    = datetime.strptime(scrape_date_str, "%Y-%m-%d") if scrape_date_str else datetime.now(timezone.utc)
            target_year  = scrape_dt.year
            target_month = scrape_dt.month

            # Build 3-month windows for period comparison.
            # "Current" period = last 3 months; "prior" = 3 months before that.
            current_period: dict[int, int] = {}
            prior_period:   dict[int, int] = {}
            for i in range(3):
                m = (target_month - i - 1) % 12 + 1
                y = target_year if target_month - i - 1 >= 0 else target_year - 1
                current_period[m] = monthly_counts_by_ym.get((y, m), 0)

            for i in range(3):
                m = (target_month - i - 4) % 12 + 1
                y = target_year if target_month - i - 4 >= 0 else target_year - 1
                prior_period[m] = monthly_counts_by_ym.get((y, m), 0)

            # Try year-over-year first (requires data from same month last year).
            yoy = seasonality.year_over_year(monthly_counts_by_ym, target_month, target_year)
            if yoy is not None:
                monthly_volume_trend = yoy["trend"]
                seasonality_adjusted = yoy["seasonally_adjusted"]
                comparison_method    = yoy["comparison_method"]
            else:
                period_result        = seasonality.compare_periods(current_period, prior_period)
                monthly_volume_trend = period_result["trend"]
                seasonality_adjusted = period_result["seasonally_adjusted"]
                comparison_method    = period_result["comparison_method"]
        except Exception as exc:
            logger.warning("Monthly trend calculation failed: %s", exc)

    # Apply recency gap adjustment.
    if days_since_last is not None:
        if days_since_last < 7:
            review_velocity_score = min(100, review_velocity_score + 5)
        elif days_since_last > 45:
            review_gap_alert = True
            review_velocity_score = max(0, review_velocity_score - 20)
        elif days_since_last > 30:
            review_velocity_score = max(0, review_velocity_score - 10)

    # Apply monthly volume trend adjustment.
    if monthly_volume_trend == "growing":
        review_velocity_score = min(100, review_velocity_score + 10)
    elif monthly_volume_trend == "declining":
        review_velocity_score = max(0, review_velocity_score - 15)
    elif monthly_volume_trend == "sharply_declining":
        review_velocity_score = max(0, review_velocity_score - 25)

    # Apply 1-star spike adjustment.
    if one_star_pct_60d is not None and one_star_pct_60d > 30:
        one_star_spike = True
        review_velocity_score = max(0, review_velocity_score - 15)

    # Apply owner response rate adjustment.
    if owner_resp_rate > 50:
        review_velocity_score = min(100, review_velocity_score + 5)
    elif owner_resp_rate < 20:
        review_velocity_score = max(0, review_velocity_score - 5)

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

    # Phase 6b enhanced rating flags and adjustments
    rating_deterioration = False
    source_divergence    = False
    ninety_day_slope     = "stable"

    avg_last_60  = velocity_metrics.get("avg_rating_last_60d")
    avg_prior_60 = velocity_metrics.get("avg_rating_prior_60d")

    # 90-day slope: last 60d vs prior 60d rating average.
    if avg_last_60 is not None and avg_prior_60 is not None:
        slope_delta = avg_last_60 - avg_prior_60
        if slope_delta >= 0.2:
            ninety_day_slope = "improving"
            rating_trend_score = min(100, rating_trend_score + 10)
        elif slope_delta <= -0.5:
            ninety_day_slope = "sharp_decline"
            rating_trend_score = max(0, rating_trend_score - 25)
        elif slope_delta <= -0.2:
            ninety_day_slope = "declining"
            rating_trend_score = max(0, rating_trend_score - 15)
        else:
            ninety_day_slope = "stable"
    elif avg_last_60 is None and avg_prior_60 is None:
        ninety_day_slope = "stable"  # insufficient data — no penalty

    # Recent vs lifetime gap.
    if avg_last_60 is not None and google_rating is not None:
        gap = google_rating - avg_last_60  # positive = recent is below lifetime
        if gap >= 0.5:
            rating_deterioration = True
            rating_trend_score = max(0, rating_trend_score - 10)
        elif gap <= -0.5:
            rating_trend_score = min(100, rating_trend_score + 5)

    # Cross-source divergence: compare Google vs Foursquare on 0-5 scale.
    if google_rating is not None and foursquare_rating is not None:
        fsq_on_5 = foursquare_rating / 2.0
        divergence = abs(google_rating - fsq_on_5)
        if divergence > 1.0:
            source_divergence = True
            rating_trend_score = max(0, rating_trend_score - 10)
        elif divergence >= 0.5:
            source_divergence = True
            rating_trend_score = max(0, rating_trend_score - 5)

    # Review count confidence multiplier.
    review_count_confidence: str
    if google_review_count >= 200:
        review_count_confidence = "high"
        confidence_multiplier   = 1.0
    elif google_review_count >= 50:
        review_count_confidence = "medium"
        confidence_multiplier   = 0.85
    else:
        review_count_confidence = "low"
        confidence_multiplier   = 0.70

    rating_trend_score = int(rating_trend_score * confidence_multiplier)

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

    # ── financial_risk_score (Phase 8) ────────────────────────────────────────
    # Starts at 100 (no risk). Penalties applied only when data confirms negative
    # signals. Absence of SBA or tax data is neutral — no penalty.
    financial_risk_score  = 100
    sba_default           = False
    repeated_sba_borrowing = False
    tax_delinquent        = False
    sba_loan_count        = 0
    sba_latest_status_val = "none_found"
    sba_latest_amount_val: Optional[float] = None
    tax_delinquency_years = 0

    if sba_row:
        sba_payload = sba_row.payload
        # status='no_data_available' means the API was unreachable — treat as neutral.
        if sba_payload.get("status") != "no_data_available":
            sba_loan_count        = sba_payload.get("loan_count", 0)
            sba_latest_status_val = sba_payload.get("sba_latest_status", "none_found")
            sba_latest_amount_val = sba_payload.get("sba_latest_amount")

            if sba_payload.get("has_chargeoff"):
                sba_default = True
                financial_risk_score -= 40  # charged off / defaulted: -40

            elif sba_latest_status_val == "active":
                financial_risk_score -= 10  # active SBA debt: -10

            if sba_payload.get("repeated_borrowing"):
                repeated_sba_borrowing = True
                financial_risk_score  -= 15  # 2+ loans in history: -15

    if prop_tax_row:
        pt = prop_tax_row.payload
        if pt.get("status") != "no_data_available" and pt.get("delinquent"):
            years = pt.get("years_delinquent", 0) or 0
            tax_delinquency_years = years
            if years >= 2:
                tax_delinquent       = True
                financial_risk_score -= 35  # delinquent 2+ years: -35
            elif years == 1:
                tax_delinquent       = True
                financial_risk_score -= 20  # delinquent 1 year: -20

    financial_risk_score = max(0, financial_risk_score)

    # ── overall_score ──────────────────────────────────────────────────────────
    overall_score = int(
        review_velocity_score * _VELOCITY_WEIGHT
        + rating_trend_score  * _RATING_WEIGHT
        + operational_score   * _OPERATIONAL_WEIGHT
        + financial_risk_score * _FINANCIAL_WEIGHT
    )

    # ── score caps ─────────────────────────────────────────────────────────────
    # sba_default caps at 45; tax delinquent 2+ years caps at 55.
    if sba_default:
        overall_score = min(overall_score, 45)
    if tax_delinquent and tax_delinquency_years >= 2:
        overall_score = min(overall_score, 55)

    # ── score_factors ──────────────────────────────────────────────────────────
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

    if days_since_last is not None:
        if days_since_last < 7:
            recency_impact = "positive"
        elif days_since_last <= 30:
            recency_impact = "neutral"
        else:
            recency_impact = "negative"
        review_velocity_factors.append({
            "signal": "recency_gap",
            "label": "Days since last review",
            "value": f"{days_since_last} days",
            "date": google_scraped_date or "",
            "impact": recency_impact,
            "weight": "medium",
            "flag": "review_gap_alert" if review_gap_alert else None,
        })

    if monthly_volume_trend != "insufficient_data":
        mv_impact = "positive" if monthly_volume_trend == "growing" else (
            "negative" if monthly_volume_trend in ("declining", "sharply_declining") else "neutral"
        )
        review_velocity_factors.append({
            "signal": "monthly_volume_trend",
            "label": "Monthly review trend",
            "value": monthly_volume_trend.replace("_", " ").title(),
            "date": google_scraped_date or "",
            "impact": mv_impact,
            "weight": "high",
            "seasonallyAdjusted": seasonality_adjusted,
            "comparisonMethod": comparison_method,
        })

    if one_star_pct_60d is not None:
        review_velocity_factors.append({
            "signal": "one_star_spike",
            "label": "1-star reviews (last 60 days)",
            "value": f"{one_star_pct_60d}% of recent reviews",
            "date": google_scraped_date or "",
            "impact": "negative" if one_star_spike else "neutral",
            "weight": "medium",
            "flag": "one_star_spike" if one_star_spike else None,
        })

    review_velocity_factors.append({
        "signal": "owner_response_rate",
        "label": "Owner response rate (90 days)",
        "value": f"{owner_resp_rate}%",
        "date": google_scraped_date or "",
        "impact": "positive" if owner_resp_rate > 50 else ("neutral" if owner_resp_rate >= 20 else "negative"),
        "weight": "low",
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

    if avg_last_60 is not None or avg_prior_60 is not None:
        slope_label = ninety_day_slope.replace("_", " ").title()
        slope_impact = "positive" if ninety_day_slope == "improving" else (
            "negative" if ninety_day_slope in ("declining", "sharp_decline") else "neutral"
        )
        rating_trend_factors.append({
            "signal": "ninety_day_slope",
            "label": "90-day rating slope",
            "value": slope_label,
            "date": google_scraped_date or "",
            "impact": slope_impact,
            "weight": "high",
        })

    if avg_last_60 is not None and google_rating is not None:
        gap = google_rating - avg_last_60
        gap_impact = "negative" if rating_deterioration else ("positive" if gap <= -0.5 else "neutral")
        rating_trend_factors.append({
            "signal": "recent_vs_lifetime_gap",
            "label": "Recent vs lifetime rating",
            "value": f"{avg_last_60:.1f} recent vs {google_rating:.1f} lifetime",
            "date": google_scraped_date or "",
            "impact": gap_impact,
            "weight": "medium",
            "flag": "rating_deterioration" if rating_deterioration else None,
        })

    if source_divergence:
        fsq_on_5 = (foursquare_rating or 0) / 2.0
        rating_trend_factors.append({
            "signal": "cross_source_divergence",
            "label": "Cross-source divergence",
            "value": f"Google {google_rating:.1f} vs Foursquare {fsq_on_5:.1f} (on 5.0 scale)",
            "date": google_scraped_date or "",
            "impact": "negative",
            "weight": "medium",
            "flag": "source_divergence",
        })

    rating_trend_factors.append({
        "signal": "review_count_confidence",
        "label": "Rating confidence",
        "value": f"{review_count_confidence.title()} ({google_review_count:,} reviews)",
        "date": google_scraped_date or "",
        "impact": "positive" if review_count_confidence == "high" else ("neutral" if review_count_confidence == "medium" else "negative"),
        "weight": "low",
    })

    operational_factors: list[dict] = []

    if inspection_records_raw:
        latest_insp = inspection_records_raw[0]
        insp_score  = int(float(latest_insp.get("score", 0)))
        insp_date   = (latest_insp.get("insp_date") or "")[:10]

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
        completeness    = hours_payload.get("hours_completeness", 0)
        operational_factors.append({
            "signal": "hours_completeness",
            "label": "Hours on file",
            "value": f"{days_with_hours}/7 days",
            "date": hours_scraped_date or "",
            "impact": "positive" if completeness >= 100 else ("neutral" if completeness >= 70 else "negative"),
            "weight": "medium",
        })

    # ── financial_factors ─────────────────────────────────────────────────────
    financial_factors: list[dict] = []

    if sba_row and sba_row.payload.get("status") != "no_data_available":
        loan_count_val = sba_row.payload.get("loan_count", 0)
        if sba_default:
            chargeoff_date = ""
            for loan in (sba_row.payload.get("matched_loans") or []):
                if loan.get("LoanStatus") == "CHGOFF" and loan.get("ChargeOffDate"):
                    chargeoff_date = f" (charged off {str(loan['ChargeOffDate'])[:10]})"
                    break
            amount_str = f"${sba_latest_amount_val:,.0f}" if sba_latest_amount_val else "unknown amount"
            sba_value  = f"{loan_count_val} charged-off loan — {amount_str}{chargeoff_date}"
            sba_impact = "negative"
            sba_flag   = "sba_default"
        elif sba_latest_status_val == "active":
            amount_str = f"${sba_latest_amount_val:,.0f}" if sba_latest_amount_val else "unknown amount"
            latest_date = ""
            loans = sba_row.payload.get("matched_loans") or []
            if loans:
                latest_date = f" approved {str(loans[0].get('ApprovalDate', ''))[:4]}"
            sba_value  = f"{loan_count_val} active loan — {amount_str}{latest_date}"
            sba_impact = "negative"
            sba_flag   = "repeated_sba_borrowing" if repeated_sba_borrowing else None
        elif sba_latest_status_val == "paid_in_full":
            sba_value  = f"{loan_count_val} paid-in-full loan(s)"
            sba_impact = "neutral" if not repeated_sba_borrowing else "negative"
            sba_flag   = "repeated_sba_borrowing" if repeated_sba_borrowing else None
        else:
            sba_value  = "No SBA loans found"
            sba_impact = "positive"
            sba_flag   = None

        financial_factors.append({
            "signal": "sba_loans",
            "label":  "SBA loan history",
            "value":  sba_value,
            "impact": sba_impact,
            "weight": "medium",
            "flag":   sba_flag,
        })
    else:
        financial_factors.append({
            "signal": "sba_loans",
            "label":  "SBA loan history",
            "value":  "No public data available" if not sba_row else "No SBA loans found",
            "impact": "neutral",
            "weight": "medium",
            "flag":   None,
        })

    if prop_tax_row and prop_tax_row.payload.get("status") != "no_data_available":
        if tax_delinquent:
            tax_value  = f"Delinquent — {tax_delinquency_years} year{'s' if tax_delinquency_years != 1 else ''} past due"
            tax_impact = "negative"
            tax_flag   = "tax_delinquent"
        else:
            tax_value  = "Current — no delinquency"
            tax_impact = "positive"
            tax_flag   = None
        financial_factors.append({
            "signal": "property_tax",
            "label":  "Business property tax",
            "value":  tax_value,
            "impact": tax_impact,
            "weight": "medium",
            "flag":   tax_flag,
        })
    else:
        financial_factors.append({
            "signal": "property_tax",
            "label":  "Business property tax",
            "value":  "No public data available",
            "impact": "neutral",
            "weight": "medium",
            "flag":   None,
        })

    score_factors = {
        "reviewVelocity": review_velocity_factors,
        "ratingTrend":    rating_trend_factors,
        "operational":    operational_factors,
        "financial":      financial_factors,
    }

    # ── Monthly review counts for sparkline (last 12 months) ──────────────────
    # Stored in score_factors so the API can pass them to the demo UI.
    if monthly_from_rev:
        # Sort by key desc and take 12 most recent months.
        sorted_months = sorted(monthly_from_rev.keys(), reverse=True)[:12]
        score_factors["monthlyReviewCounts"] = {m: monthly_from_rev[m] for m in sorted(sorted_months)}

    # ── Write to health_scores ─────────────────────────────────────────────────
    session.execute(
        text(
            """
            INSERT INTO health_scores (
                restaurant_id,
                review_velocity_score, rating_trend_score,
                operational_score,     overall_score,
                score_factors,
                review_gap_alert,      one_star_spike,
                rating_deterioration,  source_divergence,
                ninety_day_slope,      days_since_last_review,
                owner_response_rate,   monthly_volume_trend,
                review_count_confidence, seasonality_adjusted,
                comparison_method,
                financial_risk_score,  sba_default,
                repeated_sba_borrowing, tax_delinquent,
                sba_loan_count,        sba_latest_status,
                sba_latest_amount,     tax_delinquency_years
            ) VALUES (
                :restaurant_id,
                :review_velocity_score, :rating_trend_score,
                :operational_score,     :overall_score,
                CAST(:score_factors AS jsonb),
                :review_gap_alert,      :one_star_spike,
                :rating_deterioration,  :source_divergence,
                :ninety_day_slope,      :days_since_last_review,
                :owner_response_rate,   :monthly_volume_trend,
                :review_count_confidence, :seasonality_adjusted,
                :comparison_method,
                :financial_risk_score,  :sba_default,
                :repeated_sba_borrowing, :tax_delinquent,
                :sba_loan_count,        :sba_latest_status,
                :sba_latest_amount,     :tax_delinquency_years
            )
            """
        ),
        {
            "restaurant_id":            restaurant_id,
            "review_velocity_score":    review_velocity_score,
            "rating_trend_score":       rating_trend_score,
            "operational_score":        operational_score,
            "overall_score":            overall_score,
            "score_factors":            json.dumps(score_factors),
            "review_gap_alert":         review_gap_alert,
            "one_star_spike":           one_star_spike,
            "rating_deterioration":     rating_deterioration,
            "source_divergence":        source_divergence,
            "ninety_day_slope":         ninety_day_slope,
            "days_since_last_review":   days_since_last,
            "owner_response_rate":      owner_resp_rate,
            "monthly_volume_trend":     monthly_volume_trend,
            "review_count_confidence":  review_count_confidence,
            "seasonality_adjusted":     seasonality_adjusted,
            "comparison_method":        comparison_method,
            "financial_risk_score":     financial_risk_score,
            "sba_default":              sba_default,
            "repeated_sba_borrowing":   repeated_sba_borrowing,
            "tax_delinquent":           tax_delinquent,
            "sba_loan_count":           sba_loan_count,
            "sba_latest_status":        sba_latest_status_val,
            "sba_latest_amount":        sba_latest_amount_val,
            "tax_delinquency_years":    tax_delinquency_years,
        },
    )
    session.commit()

    scores = {
        "review_velocity_score": review_velocity_score,
        "rating_trend_score":    rating_trend_score,
        "operational_score":     operational_score,
        "financial_risk_score":  financial_risk_score,
        "operational_components": {
            "health_inspection":  health_component,
            "tabc_license":       tabc_component,
            "hours_completeness": hours_component,
        },
        "staffing_score": None,
        "overall_score":  overall_score,
        "score_factors":  score_factors,
        # Phase 6b flags
        "review_gap_alert":        review_gap_alert,
        "one_star_spike":          one_star_spike,
        "rating_deterioration":    rating_deterioration,
        "source_divergence":       source_divergence,
        "ninety_day_slope":        ninety_day_slope,
        "days_since_last_review":  days_since_last,
        "owner_response_rate":     owner_resp_rate,
        "monthly_volume_trend":    monthly_volume_trend,
        "review_count_confidence": review_count_confidence,
        "seasonality_adjusted":    seasonality_adjusted,
        "comparison_method":       comparison_method,
        # Phase 8 financial risk flags
        "sba_default":             sba_default,
        "repeated_sba_borrowing":  repeated_sba_borrowing,
        "tax_delinquent":          tax_delinquent,
        "sba_loan_count":          sba_loan_count,
        "sba_latest_status":       sba_latest_status_val,
        "sba_latest_amount":       sba_latest_amount_val,
        "tax_delinquency_years":   tax_delinquency_years,
    }
    logger.info(
        "v2 scored restaurant_id=%s overall=%s financial=%s sba_default=%s tax_delinquent=%s",
        restaurant_id, overall_score, financial_risk_score, sba_default, tax_delinquent,
    )
    return scores
