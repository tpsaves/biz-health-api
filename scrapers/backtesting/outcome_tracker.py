"""Backtesting Part 4 — Outcome Tracker.

Reads all prospective cohort restaurants where outcome_90d or outcome_180d
is null, checks their current status from live data sources, and updates
the backtest_cohort outcome fields.

Outcome values: open, reduced_hours, closed_temporarily, closed_permanently, unknown

Scheduled: weekly Sunday 3:00 AM UTC.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_GOOGLE_DETAILS_URL = "https://places.googleapis.com/v1/places/{}"
_GOOGLE_SEARCH_URL  = "https://places.googleapis.com/v1/places:searchText"
_TABC_URL           = "https://data.texas.gov/resource/kguh-7q9z.json"

_REVIEW_GAP_DAYS = 45  # Flag if no new reviews in 45+ days.


# ── Main entry point ──────────────────────────────────────────────────────────

def run_outcome_tracker(session: Session) -> dict:
    """Check and update outcomes for all prospective cohort entries with missing data."""
    google_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")

    # Load prospective cohort entries that need outcome updates.
    rows = session.execute(
        text(
            """
            SELECT
                bc.id           AS cohort_id,
                bc.restaurant_id,
                bc.baseline_date,
                bc.outcome_90d,
                bc.outcome_180d,
                r.name,
                r.city,
                r.google_place_id
            FROM backtest_cohort bc
            JOIN restaurants r ON r.id = bc.restaurant_id
            WHERE bc.cohort_type = 'prospective'
              AND (bc.outcome_90d IS NULL OR bc.outcome_180d IS NULL)
            ORDER BY bc.baseline_date
            """
        )
    ).fetchall()

    updated_90d = 0
    updated_180d = 0
    today = date.today()

    for row in rows:
        baseline = (
            datetime.strptime(str(row.baseline_date), "%Y-%m-%d").date()
            if isinstance(row.baseline_date, str) else row.baseline_date
        )

        days_since = (today - baseline).days
        needs_90d  = row.outcome_90d is None and days_since >= 90
        needs_180d = row.outcome_180d is None and days_since >= 180

        if not (needs_90d or needs_180d):
            continue

        # Check current status from available sources.
        outcome = _determine_outcome(
            google_place_id = row.google_place_id,
            name            = row.name,
            city            = row.city or "",
            restaurant_id   = str(row.restaurant_id),
            baseline_date   = baseline,
            google_key      = google_key,
            session         = session,
        )

        updates: dict = {}

        if needs_90d:
            updates["outcome_90d"]      = outcome
            updates["outcome_90d_date"] = today.isoformat()
            updated_90d += 1

        if needs_180d:
            updates["outcome_180d"]      = outcome
            updates["outcome_180d_date"] = today.isoformat()
            updated_180d += 1

        if updates:
            _update_cohort(str(row.cohort_id), updates, session)
            logger.info(
                "[outcome] %s — outcome=%s (90d_updated=%s, 180d_updated=%s)",
                row.name, outcome, needs_90d, needs_180d,
            )

    summary = {
        "rows_checked":  len(rows),
        "updated_90d":   updated_90d,
        "updated_180d":  updated_180d,
    }
    logger.info("Outcome tracker complete: %s", summary)
    return summary


# ── Status detection ──────────────────────────────────────────────────────────

def _determine_outcome(
    google_place_id: Optional[str],
    name: str,
    city: str,
    restaurant_id: str,
    baseline_date: date,
    google_key: str,
    session: Session,
) -> str:
    """Determine the current operational status of a restaurant.

    Checks sources in order of reliability:
    1. Google Places businessStatus  → closed_permanently, closed_temporarily, open
    2. TABC cancellation             → closed_temporarily or closed_permanently
    3. Review recency gap            → reduced_hours flag
    4. Hours change count            → reduced_hours flag
    Falls through to 'unknown' if no signal is conclusive.
    """
    # ── 1. Google Places status ──────────────────────────────────────────────
    if google_place_id and google_key:
        gstatus = _check_google_status(google_place_id, google_key)
        if gstatus == "CLOSED_PERMANENTLY":
            return "closed_permanently"
        if gstatus == "CLOSED_TEMPORARILY":
            return "closed_temporarily"

    # ── 2. TABC cancellation ─────────────────────────────────────────────────
    tabc_status = _check_tabc_status(name, city)
    if tabc_status == "cancelled":
        return "closed_permanently"
    if tabc_status == "suspended":
        return "closed_temporarily"

    # ── 3. Review recency gap ────────────────────────────────────────────────
    recency_gap = _check_review_gap(restaurant_id, baseline_date, session)
    if recency_gap:
        return "reduced_hours"

    # ── 4. Hours reduction since baseline ────────────────────────────────────
    if _check_hours_reduction(restaurant_id, baseline_date, session):
        return "reduced_hours"

    # Google confirmed operational.
    if google_place_id and google_key and gstatus == "OPERATIONAL":
        return "open"

    return "unknown"


def _check_google_status(place_id: str, api_key: str) -> Optional[str]:
    """Return Google Places businessStatus string or None on failure."""
    try:
        resp = httpx.get(
            _GOOGLE_DETAILS_URL.format(place_id),
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "businessStatus",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("businessStatus")
    except Exception as exc:
        logger.warning("[outcome] Google Places check failed for %s: %s", place_id, exc)
        return None


def _check_tabc_status(name: str, city: str) -> Optional[str]:
    """Check TABC for cancelled or suspended licence status."""
    try:
        resp = httpx.get(
            _TABC_URL,
            params={
                "$where": (
                    f"(upper(aimstradename) like upper('%{name[:25]}%') OR "
                    f"upper(aimsownername) like upper('%{name[:25]}%')) "
                    f"AND upper(aimslicensecity) = upper('{city}')"
                ),
                "$select": "aimslicensestatus,aimscancellationdate",
                "$order": "aimslicenseissuedate DESC",
                "$limit": 1,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        records = resp.json()
        if not records:
            return None
        status = (records[0].get("aimslicensestatus") or "").lower()
        if "cancel" in status or "expire" in status:
            return "cancelled"
        if "suspend" in status:
            return "suspended"
        return "active"
    except Exception as exc:
        logger.warning("[outcome] TABC check failed for %s %s: %s", name, city, exc)
        return None


def _check_review_gap(restaurant_id: str, baseline_date: date, session: Session) -> bool:
    """Return True if there have been no new reviews in 45+ days since baseline."""
    cutoff = date.today() - timedelta(days=_REVIEW_GAP_DAYS)
    if baseline_date > cutoff:
        return False  # Not enough time has passed yet.

    row = session.execute(
        text(
            """
            SELECT (payload->>'days_since_last_review')::int AS days_gap
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'google_places'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    if row and row.days_gap is not None:
        return row.days_gap > _REVIEW_GAP_DAYS
    return False


def _check_hours_reduction(restaurant_id: str, baseline_date: date, session: Session) -> bool:
    """Return True if hours_monitor detected a significant reduction since baseline."""
    row = session.execute(
        text(
            """
            SELECT payload->>'hours_change_count' AS change_count
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'hours_monitor'
              AND scraped_at > :baseline
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id, "baseline": baseline_date.isoformat()},
    ).one_or_none()

    if row and row.change_count:
        try:
            return int(row.change_count) >= 3
        except ValueError:
            pass
    return False


# ── DB update ─────────────────────────────────────────────────────────────────

def _update_cohort(cohort_id: str, updates: dict, session: Session) -> None:
    """Apply outcome field updates to a backtest_cohort row."""
    if not updates:
        return

    # Build SET clause dynamically from the updates dict.
    set_parts = [f"{col} = :{col}" for col in updates]
    params = {"id": cohort_id, **updates}

    session.execute(
        text(f"UPDATE backtest_cohort SET {', '.join(set_parts)} WHERE id = :id"),
        params,
    )
    session.commit()
