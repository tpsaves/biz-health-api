"""Backtesting Part 3 — Historical Signal Reconstructor.

For each closed restaurant, reconstructs what the scoring signals looked like at
T-90 and T-180 before the known closure date. Writes results to backtest_cohort
with cohort_type='retrospective'.

For each time cutoff:
  - Reviews:          Outscraper data filtered to reviews published before cutoff
  - Health inspection: Dallas OpenData records filtered by inspection_date < cutoff
  - TABC:             TABC record, adjusted if cancellation date is after cutoff
  - Financial / hours: Not reconstructible historically — defaulted to neutral
"""

import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_OUTSCRAPER_URL  = "https://api.app.outscraper.com/maps/reviews-v3"
_OUTSCRAPER_POLL = "https://api.app.outscraper.com/requests/{}"
_DALLAS_OD_URL   = "https://www.dallasopendata.com/resource/dri5-wcct.json"
_TABC_URL        = "https://data.texas.gov/resource/kguh-7q9z.json"

_MAX_REVIEWS     = 520
_POLL_RETRIES    = 20
_POLL_INTERVAL   = 6  # seconds

_HEALTH_WEIGHT = 0.50
_TABC_WEIGHT   = 0.30
_HOURS_WEIGHT  = 0.20

_LICENSE_TYPE_SCORES = {"MB": 100, "BG": 85, "BQ": 75, "P": 70}
_DEFAULT_LICENSE_SCORE = 65

_MAX_REVIEW_COUNT = 2000


# ── Public entry point ────────────────────────────────────────────────────────

def run_historical_reconstruction(session: Session) -> dict:
    """Reconstruct T-90 and T-180 scores for all restaurants in closed_restaurants.

    Skips any restaurant that already has a retrospective backtest_cohort row.
    """
    closed = session.execute(
        text(
            """
            SELECT id, name, address, city, google_place_id, closure_date
            FROM closed_restaurants
            WHERE closure_date IS NOT NULL
            ORDER BY created_at
            """
        )
    ).fetchall()

    reconstructed = 0
    failed = 0

    for row in closed:
        try:
            _reconstruct_one(row, session)
            reconstructed += 1
            logger.info("[reconstructor] Done: %s", row.name)
        except Exception as exc:
            failed += 1
            logger.error("[reconstructor] Failed: %s — %s", row.name, exc)

    summary = {
        "total_closed_with_dates": len(closed),
        "reconstructed":           reconstructed,
        "failed":                  failed,
    }
    logger.info("Historical reconstruction complete: %s", summary)
    return summary


def reconstruct_one_by_id(closed_id: str, session: Session) -> dict:
    """Reconstruct a single closed restaurant by its closed_restaurants.id."""
    row = session.execute(
        text(
            "SELECT id, name, address, city, google_place_id, closure_date "
            "FROM closed_restaurants WHERE id = :id"
        ),
        {"id": closed_id},
    ).one()
    return _reconstruct_one(row, session)


# ── Core reconstruction logic ─────────────────────────────────────────────────

def _reconstruct_one(row, session: Session) -> dict:
    """Fetch all available historical signals and score at T-90 and T-180."""
    closure_dt = (
        datetime.strptime(str(row.closure_date), "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
    )

    cutoffs = {
        "T-90":  closure_dt - timedelta(days=90),
        "T-180": closure_dt - timedelta(days=180),
    }

    # Fetch all signals once, then filter per cutoff.
    all_reviews = _fetch_outscraper_reviews(row.google_place_id, row.name, row.city or "")
    all_inspections = _fetch_dallas_inspections(row.name)
    tabc_record = _fetch_tabc_record(row.name, row.city or "")

    results = {}
    restaurant_id = _ensure_restaurant_in_db(row, session)

    for label, cutoff_dt in cutoffs.items():
        scores = _score_at_cutoff(
            restaurant_id   = restaurant_id,
            name            = row.name,
            closure_date    = row.closure_date,
            cutoff_dt       = cutoff_dt,
            all_reviews     = all_reviews,
            all_inspections = all_inspections,
            tabc_record     = tabc_record,
        )

        _upsert_retrospective(restaurant_id, row.closure_date, label, scores, session)
        results[label] = scores

    return results


def _score_at_cutoff(
    restaurant_id: str,
    name: str,
    closure_date,
    cutoff_dt: datetime,
    all_reviews: list[dict],
    all_inspections: list[dict],
    tabc_record: Optional[dict],
) -> dict:
    """Compute a risk score using only signals available before cutoff_dt.

    Follows the same component structure as engine_v2 but works on
    pre-filtered payloads rather than DB queries.
    """
    cutoff_date = cutoff_dt.date()

    # ── review_velocity_score ────────────────────────────────────────────────
    pre_cutoff_reviews = [
        r for r in all_reviews
        if _review_dt(r) is not None and _review_dt(r).date() <= cutoff_date
    ]

    review_count = len(pre_cutoff_reviews)
    review_velocity_score = min(100, int(review_count / _MAX_REVIEW_COUNT * 100))

    # Days since last review relative to cutoff.
    review_dates = sorted(
        [_review_dt(r) for r in pre_cutoff_reviews if _review_dt(r)], reverse=True
    )
    days_since_last: Optional[int] = None
    if review_dates:
        days_since_last = (cutoff_dt - review_dates[0]).days
        if days_since_last > 45:
            review_velocity_score = max(0, review_velocity_score - 20)
        elif days_since_last > 30:
            review_velocity_score = max(0, review_velocity_score - 10)

    # Monthly volume trend at cutoff.
    monthly_counts = _monthly_breakdown_at_cutoff(pre_cutoff_reviews, cutoff_dt)
    volume_trend = _compute_volume_trend(monthly_counts, cutoff_dt)
    if volume_trend == "sharply_declining":
        review_velocity_score = max(0, review_velocity_score - 25)
    elif volume_trend == "declining":
        review_velocity_score = max(0, review_velocity_score - 15)
    elif volume_trend == "growing":
        review_velocity_score = min(100, review_velocity_score + 10)

    # ── rating_trend_score ───────────────────────────────────────────────────
    ratings = [
        float(r["review_rating"])
        for r in pre_cutoff_reviews
        if isinstance(r.get("review_rating"), (int, float))
    ]
    if ratings:
        avg_rating = sum(ratings) / len(ratings)
        # Normalise 1-5 → 0-100.
        rating_trend_score = int((avg_rating - 1.0) / 4.0 * 100)
    else:
        rating_trend_score = 0

    # Review count confidence multiplier.
    if review_count >= 200:
        multiplier = 1.0
    elif review_count >= 50:
        multiplier = 0.85
    else:
        multiplier = 0.70
    rating_trend_score = int(rating_trend_score * multiplier)

    # ── operational_score ────────────────────────────────────────────────────
    # Health inspection component.
    pre_cutoff_insp = [
        i for i in all_inspections
        if i.get("inspection_date") and i["inspection_date"] <= cutoff_date.isoformat()
    ]
    health_component = 0
    inspection_trend = "insufficient_data"
    if pre_cutoff_insp:
        latest_score = int(float(pre_cutoff_insp[0].get("score", 0)))
        health_component = latest_score
        if len(pre_cutoff_insp) >= 2:
            prev = int(float(pre_cutoff_insp[1].get("score", 0)))
            if latest_score > prev + 3:
                inspection_trend = "improving"
            elif latest_score < prev - 3:
                inspection_trend = "declining"
            else:
                inspection_trend = "stable"

    # TABC component: treat as active at cutoff unless cancellation was before cutoff.
    tabc_component = 0
    if tabc_record:
        cancel_str = tabc_record.get("aimscancellationdate", "")
        if cancel_str:
            try:
                cancel_date = datetime.fromisoformat(cancel_str[:10]).date()
                if cancel_date > cutoff_date:
                    # Licence was still active at the cutoff.
                    lic_type = tabc_record.get("aimslicensetype", "")
                    tabc_component = _LICENSE_TYPE_SCORES.get(lic_type, _DEFAULT_LICENSE_SCORE)
                # else: already cancelled by cutoff — component stays 0
            except ValueError:
                lic_type = tabc_record.get("aimslicensetype", "")
                tabc_component = _LICENSE_TYPE_SCORES.get(lic_type, _DEFAULT_LICENSE_SCORE)
        else:
            lic_type = tabc_record.get("aimslicensetype", "")
            tabc_component = _LICENSE_TYPE_SCORES.get(lic_type, _DEFAULT_LICENSE_SCORE)

    # Hours: can't reconstruct historical — use neutral 70%.
    hours_component = 70

    operational_score = int(
        health_component * _HEALTH_WEIGHT
        + tabc_component  * _TABC_WEIGHT
        + hours_component * _HOURS_WEIGHT
    )

    # ── financial_risk_score ─────────────────────────────────────────────────
    # Not reconstructible historically — treat as neutral (100).
    financial_risk_score = 100

    # ── overall_score ────────────────────────────────────────────────────────
    overall_score = int(
        review_velocity_score * 0.20
        + rating_trend_score  * 0.30
        + operational_score   * 0.30
        + financial_risk_score * 0.20
    )

    # Composite cap (Phase 10): mirrors the same rule in engine_v2.
    composite_risk_cap = False
    if operational_score < 65 and review_velocity_score < 30:
        overall_score = min(overall_score, 59)
        composite_risk_cap = True

    return {
        "cutoff_date":            cutoff_date.isoformat(),
        "overall_score":          overall_score,
        "review_velocity_score":  review_velocity_score,
        "rating_trend_score":     rating_trend_score,
        "operational_score":      operational_score,
        "financial_risk_score":   financial_risk_score,
        "review_count":           review_count,
        "days_since_last_review": days_since_last,
        "volume_trend":           volume_trend,
        "health_component":       health_component,
        "tabc_component":         tabc_component,
        "inspection_trend":       inspection_trend,
        "composite_risk_cap":     composite_risk_cap,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_restaurant_in_db(row, session: Session) -> str:
    """Find or create a restaurants row for this closed restaurant."""
    place_id = row.google_place_id
    if place_id:
        existing = session.execute(
            text("SELECT id FROM restaurants WHERE google_place_id = :pid"),
            {"pid": place_id},
        ).one_or_none()
        if existing:
            return str(existing.id)

    # Try name+city match.
    existing = session.execute(
        text(
            "SELECT id FROM restaurants "
            "WHERE lower(name) = lower(:name) AND lower(city) = lower(:city) LIMIT 1"
        ),
        {"name": row.name, "city": row.city or ""},
    ).one_or_none()
    if existing:
        return str(existing.id)

    import uuid as _uuid
    new_id = str(_uuid.uuid4())
    session.execute(
        text(
            """
            INSERT INTO restaurants (id, name, address, city, state, google_place_id)
            VALUES (:id, :name, :address, :city, 'TX', :place_id)
            """
        ),
        {
            "id":       new_id,
            "name":     row.name,
            "address":  row.address or "",
            "city":     row.city or "",
            "place_id": place_id,
        },
    )
    session.commit()
    return new_id


def _upsert_retrospective(
    restaurant_id: str,
    closure_date,
    cutoff_label: str,
    scores: dict,
    session: Session,
) -> None:
    """Write one retrospective backtest_cohort row."""
    exists = session.execute(
        text(
            "SELECT 1 FROM backtest_cohort "
            "WHERE restaurant_id = :rid AND cohort_type = 'retrospective' "
            "AND notes LIKE :label LIMIT 1"
        ),
        {"rid": restaurant_id, "label": f"%{cutoff_label}%"},
    ).one_or_none()
    if exists:
        return

    overall = scores["overall_score"]
    session.execute(
        text(
            """
            INSERT INTO backtest_cohort
              (restaurant_id, cohort_type, baseline_score, baseline_risk_band,
               baseline_date, baseline_factors, closure_date, closure_source, notes)
            VALUES
              (:rid, 'retrospective', :score, :band, :cutoff_date,
               CAST(:factors AS jsonb), :closure_date, 'closed_restaurants', :notes)
            """
        ),
        {
            "rid":          restaurant_id,
            "score":        overall,
            "band":         _risk_band(overall),
            "cutoff_date":  scores["cutoff_date"],
            "factors":      json.dumps(scores),
            "closure_date": str(closure_date) if closure_date else None,
            "notes":        f"Historical reconstruction at {cutoff_label}",
        },
    )
    session.commit()


def _risk_band(score: int) -> str:
    if score >= 80:  return "low"
    if score >= 60:  return "moderate"
    if score >= 40:  return "elevated"
    return "high"


# ── API helpers ───────────────────────────────────────────────────────────────

def _fetch_outscraper_reviews(place_id: str, name: str, city: str) -> list[dict]:
    """Fetch all available reviews from Outscraper for a (possibly closed) restaurant."""
    api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
    if not api_key:
        logger.info("[reconstructor] OUTSCRAPER_API_KEY not set — skipping review data for %s", name)
        return []

    query = f"{name} {city}".strip() if city else name
    headers = {"X-API-KEY": api_key}

    try:
        resp = httpx.get(
            _OUTSCRAPER_URL,
            params={"query": query, "limit": 1, "reviewsLimit": _MAX_REVIEWS, "sort": "newest"},
            headers=headers,
            timeout=30.0,
        )
        if resp.status_code == 402:
            logger.info("[reconstructor] Outscraper account has no credits — skipping review data")
            return []
        resp.raise_for_status()
        raw = resp.json()

        if raw.get("status") in ("Pending", None) or not raw.get("data"):
            raw = _poll_outscraper(raw.get("id", ""), headers)

        return _extract_reviews(raw)

    except Exception as exc:
        logger.error("[reconstructor] Outscraper failed for %s: %s", name, exc)
        return []


def _poll_outscraper(request_id: str, headers: dict) -> dict:
    for attempt in range(_POLL_RETRIES):
        time.sleep(_POLL_INTERVAL)
        resp = httpx.get(_OUTSCRAPER_POLL.format(request_id), headers=headers, timeout=30.0)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "Success":
            return result
        if result.get("status") == "Failed":
            raise RuntimeError(f"Outscraper task {request_id} failed")
    raise RuntimeError(f"Outscraper task {request_id} timed out")


def _extract_reviews(raw: dict) -> list[dict]:
    data = raw.get("data", [])
    if not data:
        return []
    first = data[0]
    if isinstance(first, dict):
        return first.get("reviews_data", [])
    if isinstance(first, list):
        return first
    return []


def _fetch_dallas_inspections(name: str) -> list[dict]:
    """Query Dallas OpenData for all inspection records for a restaurant."""
    safe_name = name[:30].replace("'", "''")
    try:
        resp = httpx.get(
            _DALLAS_OD_URL,
            params={
                "$where": f"upper(program_identifier) like upper('%{safe_name}%')",
                "$select": "program_identifier,site_address,insp_date,score",
                "$order": "insp_date DESC",
                "$limit": 50,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        records = resp.json()
        # Normalise to common key names used by _score_at_cutoff.
        for rec in records:
            if rec.get("insp_date"):
                rec["inspection_date"] = rec["insp_date"][:10]
        return records
    except Exception as exc:
        logger.warning("[reconstructor] Dallas inspection query failed for %s: %s", name, exc)
        return []


def _fetch_tabc_record(name: str, city: str) -> Optional[dict]:
    """Fetch the most recent TABC record for the restaurant."""
    safe_name = name[:30].replace("'", "''")
    safe_city = city.replace("'", "''")
    try:
        resp = httpx.get(
            _TABC_URL,
            params={
                "$where": (
                    f"(upper(aimstradename) like upper('%{safe_name}%') OR "
                    f"upper(aimsownername) like upper('%{safe_name}%')) "
                    f"AND upper(city) = upper('{safe_city}')"
                ),
                "$limit": 1,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        records = resp.json()
        return records[0] if records else None
    except Exception as exc:
        logger.warning("[reconstructor] TABC query failed for %s: %s", name, exc)
        return None


# ── Review time helpers ───────────────────────────────────────────────────────

def _review_dt(review: dict) -> Optional[datetime]:
    ts = review.get("review_timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            pass
    dt_str = review.get("review_datetime_utc", "")
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _monthly_breakdown_at_cutoff(reviews: list[dict], cutoff_dt: datetime) -> dict[str, int]:
    """Build monthly review count dict for the 12 months before cutoff."""
    lookback = cutoff_dt - timedelta(days=366)
    counts: dict[str, int] = defaultdict(int)
    for r in reviews:
        dt = _review_dt(r)
        if dt and lookback <= dt <= cutoff_dt:
            counts[dt.strftime("%Y-%m")] += 1
    return dict(counts)


def _compute_volume_trend(monthly_counts: dict[str, int], cutoff_dt: datetime) -> str:
    """Simple period comparison: last 3 months vs prior 3 months."""
    if not monthly_counts:
        return "insufficient_data"

    def _period_total(offset_months: int, span: int) -> int:
        total = 0
        for i in range(span):
            dt = cutoff_dt - timedelta(days=30 * (offset_months + i))
            key = dt.strftime("%Y-%m")
            total += monthly_counts.get(key, 0)
        return total

    recent = _period_total(0, 3)
    prior  = _period_total(3, 3)

    if prior == 0:
        return "insufficient_data"

    ratio = recent / prior
    if ratio >= 1.15:    return "growing"
    if ratio >= 0.90:    return "stable"
    if ratio >= 0.70:    return "declining"
    return "sharply_declining"
