"""Outscraper monthly usage quota tracker.

At $3 per 1,000 records a 15,000-record cap limits monthly Outscraper spend to ~$45.
Backfill records (one-time May 25 catch-up) are tracked separately and do not
count against the monthly cap.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MONTHLY_CAP    = 15_000
COST_PER_1000  = 3.00


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _used_this_month(session: Session) -> int:
    # Exclude backfill records — they don't count against the monthly cap.
    return int(
        session.execute(
            text(
                "SELECT COALESCE(SUM(records_fetched), 0) FROM outscraper_usage "
                "WHERE month = :m AND COALESCE(is_backfill, false) = false"
            ),
            {"m": _current_month()},
        ).scalar()
        or 0
    )


def _backfill_used_this_month(session: Session) -> int:
    return int(
        session.execute(
            text(
                "SELECT COALESCE(SUM(records_fetched), 0) FROM outscraper_usage "
                "WHERE month = :m AND is_backfill = true"
            ),
            {"m": _current_month()},
        ).scalar()
        or 0
    )


def check_quota(records_requested: int, session: Session) -> dict:
    """Return whether the quota allows fetching records_requested more records.

    Returns:
        {"allowed": bool, "remaining": int, "used": int}
    """
    used      = _used_this_month(session)
    remaining = max(0, MONTHLY_CAP - used)
    allowed   = (used + records_requested) <= MONTHLY_CAP
    return {"allowed": allowed, "remaining": remaining, "used": used}


def log_usage(restaurant_id: str, records_fetched: int, session: Session, is_backfill: bool = False) -> None:
    """Insert a usage record after a successful Outscraper API call."""
    session.execute(
        text(
            """
            INSERT INTO outscraper_usage (restaurant_id, records_fetched, month, is_backfill)
            VALUES (CAST(:rid AS uuid), :records, :month, :is_backfill)
            """
        ),
        {
            "rid":         restaurant_id,
            "records":     records_fetched,
            "month":       _current_month(),
            "is_backfill": is_backfill,
        },
    )
    session.commit()


def log_backfill_usage(restaurant_id: str, records_fetched: int, session: Session) -> None:
    """Track backfill records separately — not counted against the monthly cap."""
    log_usage(restaurant_id, records_fetched, session, is_backfill=True)


def log_run_status(
    status: str,
    restaurants_completed: int,
    restaurants_skipped: int,
    records_used_before: int,
    records_used_after: int,
    session: Session,
    no_reviews: int = 0,
    failed: int = 0,
) -> None:
    """Persist a biweekly run status to outscraper_run_log for the quota history display."""
    session.execute(
        text(
            """
            INSERT INTO outscraper_run_log
              (status, restaurants_completed, restaurants_skipped,
               restaurants_no_reviews, restaurants_failed,
               records_used_before, records_used_after)
            VALUES (:status, :completed, :skipped, :no_reviews, :failed, :before, :after)
            """
        ),
        {
            "status":     status,
            "completed":  restaurants_completed,
            "skipped":    restaurants_skipped,
            "no_reviews": no_reviews,
            "failed":     failed,
            "before":     records_used_before,
            "after":      records_used_after,
        },
    )
    session.commit()


def monthly_summary(session: Session) -> dict:
    """Return current month usage summary with cost estimate."""
    used          = _used_this_month(session)
    backfill_used = _backfill_used_this_month(session)
    remaining     = max(0, MONTHLY_CAP - used)
    cost          = used / 1000 * COST_PER_1000
    return {
        "month":                 _current_month(),
        "records_used":          used,
        "records_remaining":     remaining,
        "cap":                   MONTHLY_CAP,
        "estimated_cost":        f"${cost:.2f}",
        "pct_used":              round(used / MONTHLY_CAP * 100),
        "projected_monthly":     14_980,
        "schedule":              "biweekly (1st and 15th)",
        "backfill_records_used": backfill_used,
    }
