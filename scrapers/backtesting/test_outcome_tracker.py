"""Test: Outcome Tracker.

Runs the outcome tracker against the prospective cohort, prints current
status per restaurant, and confirms outcome fields were updated in backtest_cohort.

Usage (from inside the scrapers container):
    python backtesting/test_outcome_tracker.py
"""

import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.outcome_tracker import run_outcome_tracker

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def main():
    engine = _build_engine()

    print("\n" + "=" * 60)
    print("OUTCOME TRACKER — Test Run")
    print("=" * 60)

    with Session(engine) as session:
        summary = run_outcome_tracker(session)

    print(f"\n── Outcome Tracker Summary ──")
    print(f"  Rows checked:      {summary['rows_checked']}")
    print(f"  90-day outcomes updated:   {summary['updated_90d']}")
    print(f"  180-day outcomes updated:  {summary['updated_180d']}")

    # Show current outcome distribution.
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT
                    r.name,
                    bc.baseline_date,
                    bc.baseline_score,
                    bc.outcome_90d,
                    bc.outcome_180d
                FROM backtest_cohort bc
                JOIN restaurants r ON r.id = bc.restaurant_id
                WHERE bc.cohort_type = 'prospective'
                ORDER BY bc.baseline_date DESC
                LIMIT 20
                """
            )
        ).fetchall()

        outcome_dist = session.execute(
            text(
                """
                SELECT outcome_90d, count(*) as cnt
                FROM backtest_cohort
                WHERE cohort_type = 'prospective' AND outcome_90d IS NOT NULL
                GROUP BY outcome_90d
                ORDER BY cnt DESC
                """
            )
        ).fetchall()

        eligible = session.execute(
            text(
                """
                SELECT count(*) FROM backtest_cohort bc
                WHERE cohort_type = 'prospective'
                  AND (current_date - baseline_date::date) >= 90
                """
            )
        ).scalar()

    print(f"\n── Prospective Cohort Status ──")
    print(f"  {'Restaurant':<40}  {'Baseline':<12}  {'Score':>5}  {'90d Outcome':<20}  {'180d Outcome'}")
    print("  " + "─" * 100)
    for row in rows:
        print(
            f"  {row.name:<40}  {str(row.baseline_date):<12}  {row.baseline_score:>5}  "
            f"{(row.outcome_90d or 'pending'):<20}  {row.outcome_180d or 'pending'}"
        )

    print(f"\n── 90-Day Outcome Distribution ──")
    for row in outcome_dist:
        print(f"  {row.outcome_90d or 'null':<25} {row.cnt}")

    print(f"\n── DB Validation ──")
    print(f"  Restaurants eligible for 90d outcomes: {eligible}")
    with Session(engine) as session:
        filled = session.execute(
            text(
                "SELECT count(*) FROM backtest_cohort "
                "WHERE cohort_type = 'prospective' AND outcome_90d IS NOT NULL"
            )
        ).scalar()
    print(f"  Outcomes filled: {filled}")
    print()


if __name__ == "__main__":
    main()
