"""Test: Backtest Cohort Builder.

Runs the cohort builder for Dallas and Fort Worth only as an initial test.
Prints risk band distribution and confirms at least 20 restaurants across
all four risk bands landed in backtest_cohort.

Usage (from inside the scrapers container):
    python backtesting/test_cohort_builder.py
"""

import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.cohort_builder import build_cohort

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
    print("COHORT BUILDER — Test Run (Dallas + Fort Worth)")
    print("=" * 60)

    with Session(engine) as session:
        summary = build_cohort(["Dallas", "Fort Worth"], session)

    print("\n── Build Summary ──")
    print(f"  Cities searched:     {', '.join(summary['cities_searched'])}")
    print(f"  Restaurants added:   {summary['restaurants_added']}")
    print("\n── Risk Band Distribution ──")
    band_counts = summary["band_counts"]
    for band, count in band_counts.items():
        bar = "█" * min(count, 40)
        print(f"  {band:<12} {count:>4}  {bar}")

    # Confirm DB state.
    with Session(engine) as session:
        db_counts = session.execute(
            text(
                """
                SELECT baseline_risk_band, count(*) as cnt
                FROM backtest_cohort
                WHERE cohort_type = 'prospective'
                GROUP BY baseline_risk_band
                ORDER BY baseline_risk_band
                """
            )
        ).fetchall()

        total = session.execute(
            text(
                "SELECT count(*) FROM backtest_cohort WHERE cohort_type = 'prospective'"
            )
        ).scalar()

        sample = session.execute(
            text(
                """
                SELECT r.name, r.city, bc.baseline_score, bc.baseline_risk_band
                FROM backtest_cohort bc
                JOIN restaurants r ON r.id = bc.restaurant_id
                WHERE bc.cohort_type = 'prospective'
                ORDER BY bc.created_at DESC
                LIMIT 10
                """
            )
        ).fetchall()

    print("\n── DB Risk Band Counts ──")
    bands_in_db = {row.baseline_risk_band for row in db_counts}
    for row in db_counts:
        print(f"  {row.baseline_risk_band:<12} {row.cnt}")
    print(f"  Total prospective:   {total}")

    print("\n── Sample Cohort Records ──")
    for row in sample:
        print(f"  {row.name:<40}  {row.city:<15}  score={row.baseline_score}  [{row.baseline_risk_band}]")

    # Validation checks.
    print("\n── Validation ──")
    required_bands = {"low", "moderate", "elevated", "high"}
    if required_bands.issubset(bands_in_db):
        print("  ✓ All four risk bands represented in DB")
    else:
        missing = required_bands - bands_in_db
        print(f"  ⚠ Missing risk bands: {missing}")

    if total >= 20:
        print(f"  ✓ 20+ restaurants in cohort ({total})")
    else:
        print(f"  ⚠ Need 20+ restaurants, have {total}")

    print()


if __name__ == "__main__":
    main()
