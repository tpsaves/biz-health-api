import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from scoring import seasonality
from scoring.keyword_analyzer import analyze_keywords, compute_rating_distribution, compute_response_rates
from scoring.signal_confidence import classify_tabc_confidence, classify_inspection_confidence

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
    delivery_row     = _latest("delivery_platforms")

    # ── Base review data ───────────────────────────────────────────────────────
    google_review_count = 0
    google_rating: Optional[float] = None
    google_scraped_date: Optional[str] = None
    velocity_metrics: dict = {}
    business_status: Optional[str] = None

    place_types: list[str] = []
    if google_row:
        result = google_row.payload.get("result", {})
        google_review_count = result.get("user_ratings_total") or 0
        google_rating = result.get("rating")
        business_status = result.get("business_status")
        if google_row.scraped_at:
            google_scraped_date = google_row.scraped_at.strftime("%Y-%m-%d")
        velocity_metrics = google_row.payload.get("velocity_metrics", {})
        place_types = (google_row.payload.get("v1_raw") or {}).get("types", [])

    # ── review_velocity_score ──────────────────────────────────────────────────
    review_velocity_score = min(100, int(google_review_count / _MAX_REVIEW_COUNT * 100))
    if review_velocity_score == 0 and outscraper_row:
        outscraper_total = outscraper_row.payload.get("total_reviews_fetched", 0)
        review_velocity_score = min(100, int(outscraper_total / _MAX_REVIEW_COUNT * 100))

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

    # ── Rating distribution, keywords, response rates (Phase 11) ─────────────
    # Computed at score time from reviews_data stored by the scraper.
    # Time-windowed aggregates (90d distribution, 60d keyword scan, 60d response
    # rate) must be fresh — pre-computing at scrape time would produce stale results
    # as the window slides day by day.
    _outscraper_reviews: list[dict] = []
    if outscraper_row:
        _outscraper_reviews = outscraper_row.payload.get("reviews_data", [])

    pct_5star_recent:      float | None = None
    pct_1star_recent:      float | None = None
    high_negative_rate     = False
    negative_rate_rising   = False
    bimodal_distribution   = False
    lifetime_dist: dict    = {}

    if _outscraper_reviews:
        dist          = compute_rating_distribution(_outscraper_reviews)
        recent_dist   = dist.get("recent_90d") or {}
        lifetime_dist = dist.get("lifetime")   or {}

        if recent_dist.get("count", 0) >= 5:
            pct_1star_recent = recent_dist.get("1", 0.0)
            pct_5star_recent = recent_dist.get("5", 0.0)
            pct_1star_lifetime = lifetime_dist.get("1", 0.0)
            pct_5star_lifetime = lifetime_dist.get("5", 0.0)

            if pct_1star_recent > 30:
                high_negative_rate = True
                rating_trend_score = max(0, rating_trend_score - 15)

            if pct_1star_lifetime > 0 and (pct_1star_recent - pct_1star_lifetime) >= 10:
                negative_rate_rising = True
                rating_trend_score   = max(0, rating_trend_score - 10)

            if pct_5star_lifetime > 0 and (pct_5star_lifetime - pct_5star_recent) >= 15:
                rating_trend_score = max(0, rating_trend_score - 10)

            if pct_5star_recent > 60 and pct_1star_recent > 20:
                bimodal_distribution = True
                rating_trend_score   = max(0, rating_trend_score - 10)

            if pct_5star_recent > 70:
                rating_trend_score = min(100, rating_trend_score + 5)

    # ── Keyword flags (Phase 11) ───────────────────────────────────────────────
    sanitation_flag               = False
    operational_instability_flag  = False
    ownership_change_flag         = False
    quality_decline_flag          = False
    financial_stress_flag         = False
    keyword_findings: dict        = {}

    if _outscraper_reviews:
        keyword_findings = analyze_keywords(_outscraper_reviews, cutoff_days=60)
        if keyword_findings:
            if "sanitation_risk" in keyword_findings:
                sanitation_flag    = True
                rating_trend_score = max(0, rating_trend_score - 20)

            oi_count = keyword_findings.get("operational_instability", {}).get("match_count", 0)
            if oi_count >= 2:
                operational_instability_flag = True
                rating_trend_score           = max(0, rating_trend_score - 15)

            if "ownership_change" in keyword_findings:
                ownership_change_flag = True
                rating_trend_score    = max(0, rating_trend_score - 10)

            qd_count = keyword_findings.get("quality_decline", {}).get("match_count", 0)
            if qd_count >= 3:
                quality_decline_flag = True
                rating_trend_score   = max(0, rating_trend_score - 10)

            fs_count = keyword_findings.get("financial_stress", {}).get("match_count", 0)
            if fs_count >= 3:
                financial_stress_flag = True
                rating_trend_score    = max(0, rating_trend_score - 5)

            flagged_count = sum([
                sanitation_flag, operational_instability_flag, ownership_change_flag,
                quality_decline_flag, financial_stress_flag,
            ])
            if flagged_count >= 3:
                rating_trend_score = max(0, rating_trend_score - 10)

    # ── Response rate trend (Phase 11) ────────────────────────────────────────
    response_rate_declining = False
    owner_disengaged        = False
    response_rate_recent_val: int | None = None
    response_rate_prior_val:  int | None = None

    if _outscraper_reviews:
        response_rate_recent_val, response_rate_prior_val = compute_response_rates(_outscraper_reviews)

        if response_rate_recent_val is not None and response_rate_prior_val is not None:
            drop = response_rate_prior_val - response_rate_recent_val
            if response_rate_prior_val > 30 and response_rate_recent_val == 0:
                owner_disengaged   = True
                rating_trend_score = max(0, rating_trend_score - 15)
            elif drop >= 30:
                response_rate_declining = True
                rating_trend_score      = max(0, rating_trend_score - 10)

            if response_rate_recent_val > 50 and response_rate_prior_val > 50:
                rating_trend_score = min(100, rating_trend_score + 5)

    # Review count confidence multiplier.
    review_count_confidence: str
    _confidence_review_count = google_review_count or (
        outscraper_row.payload.get("total_reviews_fetched", 0) if outscraper_row else 0
    )
    if _confidence_review_count >= 200:
        review_count_confidence = "high"
        confidence_multiplier   = 1.0
    elif _confidence_review_count >= 50:
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

    insp_city: str = ""
    if inspection_row:
        inspection_records_raw = inspection_row.payload.get("records", [])
        insp_city = inspection_row.payload.get("city", "")
        if inspection_row.scraped_at:
            insp_scraped_date = inspection_row.scraped_at.strftime("%Y-%m-%d")
        if inspection_records_raw:
            _raw_score = inspection_records_raw[0].get("score", 0) or 0
            try:
                health_component = int(float(_raw_score))
            except (ValueError, TypeError):
                health_component = 0

    insp_confidence, insp_confidence_reason, insp_city_has_portal = classify_inspection_confidence(
        insp_city, place_types, inspection_records_raw
    )

    tabc_component: int = 0
    tabc_record: Optional[dict] = None
    tabc_scraped_date: Optional[str] = None
    tabc_records: list = []

    if tabc_row:
        tabc_records = tabc_row.payload.get("records", [])
        if tabc_row.scraped_at:
            tabc_scraped_date = tabc_row.scraped_at.strftime("%Y-%m-%d")
        if tabc_records:
            tabc_record = tabc_records[0]
            license_type = tabc_record.get("aimslicensetype", "")
            tabc_component = _LICENSE_TYPE_SCORES.get(license_type, _DEFAULT_LICENSE_SCORE)

    tabc_confidence, tabc_confidence_reason = classify_tabc_confidence(place_types, tabc_records)

    hours_component: int = 0
    hours_payload: Optional[dict] = None
    hours_scraped_date: Optional[str] = None

    total_weekly_hours_val: Optional[float] = None
    hours_reduction_pct_val: Optional[float] = None
    hours_reduction = False

    if hours_row:
        hours_payload = hours_row.payload
        if hours_row.scraped_at:
            hours_scraped_date = hours_row.scraped_at.strftime("%Y-%m-%d")
        hours_component           = hours_payload.get("hours_completeness", 0)
        total_weekly_hours_val    = hours_payload.get("total_weekly_hours")
        hours_reduction_pct_val   = hours_payload.get("hours_reduction_pct")
        if hours_reduction_pct_val is not None and hours_reduction_pct_val >= 30:
            hours_reduction = True

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

    # ── delivery_platforms (Phase 9) ───────────────────────────────────────────
    # Starts neutral. Adjustments applied only when platform data is conclusive.
    # platform_unavailable means we could not reach the platform — no adjustment.
    doordash_listed:         Optional[bool] = None
    ubereats_listed:         Optional[bool] = None
    delivery_platform_count: Optional[int]  = None
    delivery_status:         str            = "unknown"
    delivery_platform_loss:  bool           = False

    if delivery_row:
        dp  = delivery_row.payload
        dd  = dp.get("doordash", {})
        ue  = dp.get("ubereats", {})

        # Only treat a result as conclusive when status is not platform_unavailable.
        dd_conclusive = dd.get("status") not in (None, "platform_unavailable")
        ue_conclusive = ue.get("status") not in (None, "platform_unavailable")

        if dd_conclusive:
            doordash_listed = dd.get("listed", False)
        if ue_conclusive:
            ubereats_listed = ue.get("listed", False)

        listed_count = sum(1 for v in [doordash_listed, ubereats_listed] if v is True)
        any_conclusive = dd_conclusive or ue_conclusive

        if any_conclusive:
            delivery_platform_count = listed_count

        # delivery_platform_loss is true when the scraper detected a previous
        # listing that has since disappeared on one or both platforms.
        delivery_platform_loss = (
            dp.get("delisted_doordash", False) or dp.get("delisted_ubereats", False)
        )

        # Determine delivery_status.
        if not any_conclusive:
            delivery_status = "unknown"
        elif delivery_platform_loss:
            delivery_status = "offline"
        elif listed_count == 2:
            delivery_status = "active"
        elif listed_count == 1:
            delivery_status = "partial"
        else:
            delivery_status = "never_listed"

        # Apply operational_score adjustments.
        if delivery_status == "active":
            operational_score = min(100, operational_score + 5)
        elif delivery_status == "offline":
            # Was listed before but now gone — strong distress signal.
            operational_score    = max(0, operational_score - 20)
        elif delivery_status == "never_listed":
            # Never listed on any reachable platform.
            operational_score    = max(0, operational_score - 10)
        # partial:  no change
        # unknown:  no adjustment (platform_unavailable)

    # ── Signal confidence adjustments ─────────────────────────────────────────
    tabc_expected_missing       = False
    inspection_expected_missing = False
    inspection_data_unavailable = False

    if tabc_confidence == 'expected_not_found':
        tabc_expected_missing = True
        operational_score = max(0, operational_score - 15)

    if insp_confidence == 'expected_not_found':
        inspection_expected_missing = True
        operational_score = max(0, operational_score - 10)
    elif insp_confidence == 'unknown' and insp_city_has_portal:
        inspection_data_unavailable = True

    # Phase 14: business status penalty (domain knowledge — not derived from any restaurant data)
    temporarily_closed = False
    permanently_closed = False
    if business_status == "TEMPORARILY_CLOSED":
        temporarily_closed = True
        operational_score = max(0, operational_score - 30)
    elif business_status == "PERMANENTLY_CLOSED":
        permanently_closed = True

    # Phase 14: hours reduction penalty (threshold: 30% — domain knowledge)
    if hours_reduction:
        operational_score = max(0, operational_score - 15)

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
    if permanently_closed:
        overall_score = min(overall_score, 20)

    # Composite cap (Phase 10): prevents strong rating_trend from masking
    # simultaneous operational + velocity weakness. Derived from backtesting
    # false negative analysis — 4 restaurants scored moderate but closed within 180 days.
    composite_risk_cap = False
    if operational_score < 65 and review_velocity_score < 30:
        overall_score = min(overall_score, 59)
        composite_risk_cap = True

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
    elif outscraper_row and outscraper_row.payload.get("total_reviews_fetched", 0) > 0:
        _os_total = outscraper_row.payload.get("total_reviews_fetched", 0)
        review_velocity_factors.append({
            "signal": "google_review_count",
            "label": "Google review volume",
            "value": f"{_os_total:,}+ reviews (via Outscraper)",
            "date": "",
            "impact": "positive" if _os_total >= 1000 else ("neutral" if _os_total >= 300 else "negative"),
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

    # Phase 11: rating distribution factor
    if pct_1star_recent is not None:
        dist_flags = []
        if high_negative_rate:     dist_flags.append("high_negative_rate")
        if negative_rate_rising:   dist_flags.append("negative_rate_rising")
        if bimodal_distribution:   dist_flags.append("bimodal_distribution")
        pct_1star_lifetime_val = lifetime_dist.get("1", 0.0)
        rating_trend_factors.append({
            "signal": "outscraper_reviews",
            "label":  "1-star review rate (last 90 days)",
            "value":  f"{pct_1star_recent:.0f}% (vs {pct_1star_lifetime_val:.0f}% lifetime)",
            "impact": "negative" if high_negative_rate or negative_rate_rising else "neutral",
            "weight": "high",
            "flag":   dist_flags[0] if dist_flags else None,
        })

    # Phase 11: keyword findings factor
    if keyword_findings:
        kw_flag = next(
            (f for f, set_val in [
                ("sanitation_flag",              sanitation_flag),
                ("operational_instability_flag", operational_instability_flag),
                ("ownership_change_flag",        ownership_change_flag),
                ("quality_decline_flag",         quality_decline_flag),
                ("financial_stress_flag",        financial_stress_flag),
            ] if set_val), None
        )
        parts = []
        for cat, data in keyword_findings.items():
            n = data.get("match_count", 0)
            cat_label = cat.replace("_", " ")
            parts.append(f"{n} mention{'s' if n != 1 else ''} of {cat_label}")
        rating_trend_factors.append({
            "signal": "outscraper_reviews",
            "label":  "Recent review keywords",
            "value":  "; ".join(parts[:3]),
            "impact": "negative" if kw_flag else "neutral",
            "weight": "medium",
            "flag":   kw_flag,
            "keywordFindings": keyword_findings,
        })

    # Phase 11: response rate trend factor
    if response_rate_recent_val is not None and response_rate_prior_val is not None:
        rr_flag = "owner_disengaged" if owner_disengaged else ("response_rate_declining" if response_rate_declining else None)
        rr_impact = "negative" if rr_flag else ("positive" if response_rate_recent_val > 50 and response_rate_prior_val > 50 else "neutral")
        rating_trend_factors.append({
            "signal": "outscraper_reviews",
            "label":  "Owner response rate trend",
            "value":  f"Dropped from {response_rate_prior_val}% to {response_rate_recent_val}% in last 60 days" if response_rate_declining or owner_disengaged else f"{response_rate_recent_val}% (last 60d) vs {response_rate_prior_val}% (prior 60d)",
            "impact": rr_impact,
            "weight": "medium",
            "flag":   rr_flag,
        })

    rating_trend_factors.append({
        "signal": "review_count_confidence",
        "label": "Rating confidence",
        "value": f"{review_count_confidence.title()} ({_confidence_review_count:,} reviews)",
        "date": google_scraped_date or "",
        "impact": "positive" if review_count_confidence == "high" else ("neutral" if review_count_confidence == "medium" else "negative"),
        "weight": "low",
    })

    operational_factors: list[dict] = []

    if not inspection_records_raw:
        if insp_confidence == 'expected_not_found':
            operational_factors.append({
                "signal": "health_inspection",
                "label": "Health inspection",
                "value": "Expected — not found. Food service establishment with working portal but no records.",
                "impact": "negative",
                "weight": "high",
                "flag": "inspection_expected_missing",
            })
        elif insp_confidence == 'unknown' and insp_city_has_portal:
            operational_factors.append({
                "signal": "health_inspection",
                "label": "Health inspection",
                "value": "No data — portal available but no records matched",
                "impact": "neutral",
                "weight": "none",
                "flag": None,
            })
        # else: no portal city or truly unknown — no factor added (no data to show)

    if inspection_records_raw:
        latest_insp = inspection_records_raw[0]
        _raw_insp   = latest_insp.get("score", 0) or 0
        try:
            insp_score = int(float(_raw_insp))
        except (ValueError, TypeError):
            insp_score = 0
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
            _raw_prev  = inspection_records_raw[1].get("score", 0) or 0
            try:
                prev_score = int(float(_raw_prev))
            except (ValueError, TypeError):
                prev_score = 0
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
    elif tabc_confidence == 'expected_not_found':
        operational_factors.append({
            "signal": "tabc_license",
            "label": "TABC license",
            "value": "Expected but not found — bar/restaurant with no active liquor license on record",
            "impact": "negative",
            "weight": "high",
            "flag": "tabc_expected_missing",
        })
    elif tabc_confidence == 'not_applicable':
        operational_factors.append({
            "signal": "tabc_license",
            "label": "TABC license",
            "value": "N/A — business type does not require a liquor license",
            "impact": "neutral",
            "weight": "none",
            "flag": None,
        })
    else:
        operational_factors.append({
            "signal": "tabc_license",
            "label": "TABC license",
            "value": "No record found — unable to determine if license required",
            "impact": "neutral",
            "weight": "none",
            "flag": None,
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

    # ── delivery_platform factors (added to operational) ──────────────────────
    if delivery_row:
        dd  = delivery_row.payload.get("doordash", {})
        ue  = delivery_row.payload.get("ubereats", {})

        def _platform_factor(platform_label: str, result: dict, delisted: bool) -> dict:
            status = result.get("status")
            if status == "platform_unavailable":
                return {
                    "signal": "delivery_platforms",
                    "label":  f"{platform_label} listing",
                    "value":  "Data unavailable",
                    "impact": "neutral",
                    "weight": "medium",
                    "flag":   None,
                }
            elif result.get("listed"):
                return {
                    "signal": "delivery_platforms",
                    "label":  f"{platform_label} listing",
                    "value":  "Active",
                    "impact": "positive",
                    "weight": "medium",
                    "flag":   None,
                }
            elif delisted:
                return {
                    "signal": "delivery_platforms",
                    "label":  f"{platform_label} listing",
                    "value":  "Removed (was active)",
                    "impact": "negative",
                    "weight": "medium",
                    "flag":   "delivery_platform_loss",
                }
            else:
                return {
                    "signal": "delivery_platforms",
                    "label":  f"{platform_label} listing",
                    "value":  "Not listed",
                    "impact": "negative",
                    "weight": "medium",
                    "flag":   None,
                }

        operational_factors.append(
            _platform_factor("DoorDash", dd, delivery_row.payload.get("delisted_doordash", False))
        )
        operational_factors.append(
            _platform_factor("Uber Eats", ue, delivery_row.payload.get("delisted_ubereats", False))
        )

    if temporarily_closed:
        operational_factors.append({
            "signal": "google_places",
            "label":  "Business status",
            "value":  "Temporarily Closed (Google Maps)",
            "impact": "negative",
            "weight": "high",
            "flag":   "temporarily_closed",
        })

    if permanently_closed:
        operational_factors.append({
            "signal": "google_places",
            "label":  "Business status",
            "value":  "Permanently Closed (Google Maps)",
            "impact": "negative",
            "weight": "high",
            "flag":   "permanently_closed",
        })

    if hours_reduction:
        operational_factors.append({
            "signal": "hours_monitor",
            "label":  "Operating hours reduced",
            "value":  f"Weekly hours down {hours_reduction_pct_val:.0f}% vs prior snapshot",
            "impact": "negative",
            "weight": "medium",
            "flag":   "hours_reduction",
        })
    elif total_weekly_hours_val is not None:
        operational_factors.append({
            "signal": "hours_monitor",
            "label":  "Weekly operating hours",
            "value":  f"{total_weekly_hours_val:.1f} hrs/week",
            "impact": "neutral",
            "weight": "low",
            "flag":   None,
        })

    if composite_risk_cap:
        operational_factors.append({
            "signal": "scoring_engine",
            "label":  "Composite risk cap applied",
            "value":  "Operational score < 65 and review velocity < 30 — elevated risk floor applied",
            "impact": "negative",
            "weight": "high",
            "flag":   "composite_risk_cap",
        })

    score_factors = {
        "reviewVelocity": review_velocity_factors,
        "ratingTrend":    rating_trend_factors,
        "operational":    operational_factors,
        "financial":      financial_factors,
    }

    # ── Monthly review counts for sparkline (last 12 months) ──────────────────
    # Stored in score_factors so the API can pass them to the demo UI.
    # Outscraper fetches up to 520 real reviews with accurate timestamps and is
    # the authoritative source for monthly counts. Google Places monthly_from_rev
    # is built from daily 5-review snapshots and produces sparse, inaccurate counts.
    if outscraper_monthly:
        sorted_keys = sorted(outscraper_monthly.keys())[-12:]
        score_factors["monthlyReviewCounts"] = {
            k: outscraper_monthly[k]["count"]
            for k in sorted_keys
            if isinstance(outscraper_monthly.get(k), dict)
        }
    elif monthly_from_rev:
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
                sba_latest_amount,     tax_delinquency_years,
                doordash_listed,       ubereats_listed,
                delivery_platform_count, delivery_status,
                delivery_platform_loss, composite_risk_cap,
                pct_5star_recent,      pct_1star_recent,
                high_negative_rate,    negative_rate_rising,
                bimodal_distribution,
                sanitation_flag,       operational_instability_flag,
                ownership_change_flag, quality_decline_flag,
                financial_stress_flag, keyword_findings,
                response_rate_declining, owner_disengaged,
                response_rate_recent,  response_rate_prior,
                tabc_confidence,       tabc_confidence_reason,
                inspection_confidence, inspection_confidence_reason,
                tabc_expected_missing, inspection_expected_missing,
                inspection_data_unavailable,
                business_status,       temporarily_closed,
                permanently_closed,    total_weekly_hours,
                hours_reduction_pct,   hours_reduction
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
                :sba_latest_amount,     :tax_delinquency_years,
                :doordash_listed,       :ubereats_listed,
                :delivery_platform_count, :delivery_status,
                :delivery_platform_loss, :composite_risk_cap,
                :pct_5star_recent,     :pct_1star_recent,
                :high_negative_rate,   :negative_rate_rising,
                :bimodal_distribution,
                :sanitation_flag,      :operational_instability_flag,
                :ownership_change_flag, :quality_decline_flag,
                :financial_stress_flag, CAST(:keyword_findings AS jsonb),
                :response_rate_declining, :owner_disengaged,
                :response_rate_recent, :response_rate_prior,
                :tabc_confidence,      :tabc_confidence_reason,
                :inspection_confidence, :inspection_confidence_reason,
                :tabc_expected_missing, :inspection_expected_missing,
                :inspection_data_unavailable,
                :business_status,      :temporarily_closed,
                :permanently_closed,   :total_weekly_hours,
                :hours_reduction_pct,  :hours_reduction
            )
            """
        ),
        {
            "restaurant_id":             restaurant_id,
            "review_velocity_score":     review_velocity_score,
            "rating_trend_score":        rating_trend_score,
            "operational_score":         operational_score,
            "overall_score":             overall_score,
            "score_factors":             json.dumps(score_factors),
            "review_gap_alert":          review_gap_alert,
            "one_star_spike":            one_star_spike,
            "rating_deterioration":      rating_deterioration,
            "source_divergence":         source_divergence,
            "ninety_day_slope":          ninety_day_slope,
            "days_since_last_review":    days_since_last,
            "owner_response_rate":       owner_resp_rate,
            "monthly_volume_trend":      monthly_volume_trend,
            "review_count_confidence":   review_count_confidence,
            "seasonality_adjusted":      seasonality_adjusted,
            "comparison_method":         comparison_method,
            "financial_risk_score":      financial_risk_score,
            "sba_default":               sba_default,
            "repeated_sba_borrowing":    repeated_sba_borrowing,
            "tax_delinquent":            tax_delinquent,
            "sba_loan_count":            sba_loan_count,
            "sba_latest_status":         sba_latest_status_val,
            "sba_latest_amount":         sba_latest_amount_val,
            "tax_delinquency_years":     tax_delinquency_years,
            "doordash_listed":           doordash_listed,
            "ubereats_listed":           ubereats_listed,
            "delivery_platform_count":   delivery_platform_count,
            "delivery_status":           delivery_status,
            "delivery_platform_loss":         delivery_platform_loss,
            "composite_risk_cap":             composite_risk_cap,
            "pct_5star_recent":               pct_5star_recent,
            "pct_1star_recent":               pct_1star_recent,
            "high_negative_rate":             high_negative_rate,
            "negative_rate_rising":           negative_rate_rising,
            "bimodal_distribution":           bimodal_distribution,
            "sanitation_flag":                sanitation_flag,
            "operational_instability_flag":   operational_instability_flag,
            "ownership_change_flag":          ownership_change_flag,
            "quality_decline_flag":           quality_decline_flag,
            "financial_stress_flag":          financial_stress_flag,
            "keyword_findings":               json.dumps(keyword_findings) if keyword_findings else None,
            "response_rate_declining":        response_rate_declining,
            "owner_disengaged":               owner_disengaged,
            "response_rate_recent":           response_rate_recent_val,
            "response_rate_prior":            response_rate_prior_val,
            "tabc_confidence":               tabc_confidence,
            "tabc_confidence_reason":        tabc_confidence_reason,
            "inspection_confidence":         insp_confidence,
            "inspection_confidence_reason":  insp_confidence_reason,
            "tabc_expected_missing":         tabc_expected_missing,
            "inspection_expected_missing":   inspection_expected_missing,
            "inspection_data_unavailable":   inspection_data_unavailable,
            "business_status":               business_status,
            "temporarily_closed":            temporarily_closed,
            "permanently_closed":            permanently_closed,
            "total_weekly_hours":            total_weekly_hours_val,
            "hours_reduction_pct":           hours_reduction_pct_val,
            "hours_reduction":               hours_reduction,
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
        # Phase 9 delivery platform flags
        "doordash_listed":          doordash_listed,
        "ubereats_listed":          ubereats_listed,
        "delivery_platform_count":  delivery_platform_count,
        "delivery_status":          delivery_status,
        "delivery_platform_loss":   delivery_platform_loss,
        # Phase 10: composite risk cap
        "composite_risk_cap":             composite_risk_cap,
        # Phase 11: rating distribution
        "pct_5star_recent":               pct_5star_recent,
        "pct_1star_recent":               pct_1star_recent,
        "high_negative_rate":             high_negative_rate,
        "negative_rate_rising":           negative_rate_rising,
        "bimodal_distribution":           bimodal_distribution,
        # Phase 11: keyword flags
        "sanitation_flag":                sanitation_flag,
        "operational_instability_flag":   operational_instability_flag,
        "ownership_change_flag":          ownership_change_flag,
        "quality_decline_flag":           quality_decline_flag,
        "financial_stress_flag":          financial_stress_flag,
        "keyword_findings":               keyword_findings,
        # Phase 11: response rate trend
        "response_rate_declining":        response_rate_declining,
        "owner_disengaged":               owner_disengaged,
        "response_rate_recent":           response_rate_recent_val,
        "response_rate_prior":            response_rate_prior_val,
        # Signal confidence
        "tabc_confidence":               tabc_confidence,
        "tabc_confidence_reason":        tabc_confidence_reason,
        "inspection_confidence":         insp_confidence,
        "inspection_confidence_reason":  insp_confidence_reason,
        "tabc_expected_missing":         tabc_expected_missing,
        "inspection_expected_missing":   inspection_expected_missing,
        "inspection_data_unavailable":   inspection_data_unavailable,
        # Phase 14
        "business_status":               business_status,
        "temporarily_closed":            temporarily_closed,
        "permanently_closed":            permanently_closed,
        "total_weekly_hours":            total_weekly_hours_val,
        "hours_reduction_pct":           hours_reduction_pct_val,
        "hours_reduction":               hours_reduction,
    }
    logger.info(
        "v2 scored restaurant_id=%s overall=%s financial=%s sba_default=%s "
        "tax_delinquent=%s delivery_status=%s delivery_loss=%s",
        restaurant_id, overall_score, financial_risk_score, sba_default,
        tax_delinquent, delivery_status, delivery_platform_loss,
    )
    return scores
