"""Test: Historical Signal Reconstructor.

Runs reconstruction for up to 10 closed restaurants, prints T-90 and T-180
scores, and shows which signals were most degraded at T-90.

Usage (from inside the scrapers container):
    python backtesting/test_reconstructor.py
"""

import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.historical_reconstructor import run_historical_reconstruction

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
    print("HISTORICAL RECONSTRUCTOR — Test Run")
    print("=" * 60)

    with Session(engine) as session:
        summary = run_historical_reconstruction(session)

    print(f"\n── Reconstruction Summary ──")
    print(f"  Closed restaurants with dates: {summary['total_closed_with_dates']}")
    print(f"  Successfully reconstructed:    {summary['reconstructed']}")
    print(f"  Failed:                        {summary['failed']}")

    # Retrieve retrospective results from DB.
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT
                    r.name,
                    bc.baseline_date,
                    bc.baseline_score,
                    bc.baseline_risk_band,
                    bc.closure_date,
                    bc.notes,
                    bc.baseline_factors
                FROM backtest_cohort bc
                JOIN restaurants r ON r.id = bc.restaurant_id
                WHERE bc.cohort_type = 'retrospective'
                ORDER BY r.name, bc.baseline_date
                LIMIT 20
                """
            )
        ).fetchall()

    # Group by restaurant name to show T-90 vs T-180 pairs.
    by_name: dict[str, list] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)

    print(f"\n── T-90 / T-180 Score Pairs ──")
    print(f"  {'Restaurant':<40}  {'Cutoff':<12}  {'Score':>5}  {'Band':<12}  {'Closure'}")
    print("  " + "─" * 90)

    for name, records in list(by_name.items())[:10]:
        for rec in records:
            label = rec.notes.split(" at ")[-1] if rec.notes else ""
            print(
                f"  {name:<40}  {str(rec.baseline_date):<12}  {rec.baseline_score:>5}  "
                f"{rec.baseline_risk_band:<12}  {str(rec.closure_date)}"
            )

        # Signal degradation at T-90 (first record for this name).
        if records and records[0].baseline_factors:
            import json
            try:
                factors = json.loads(records[0].baseline_factors) if isinstance(records[0].baseline_factors, str) else records[0].baseline_factors
                components = {
                    "review_velocity": factors.get("review_velocity_score", 0),
                    "rating_trend":    factors.get("rating_trend_score", 0),
                    "operational":     factors.get("operational_score", 0),
                    "financial_risk":  factors.get("financial_risk_score", 0),
                }
                most_degraded = min(components, key=components.get)
                print(f"    → Most degraded signal: {most_degraded} (score={components[most_degraded]})")
            except Exception:
                pass
        print()

    # DB validation.
    with Session(engine) as session:
        total_retro = session.execute(
            text("SELECT count(*) FROM backtest_cohort WHERE cohort_type = 'retrospective'")
        ).scalar()

    print(f"── DB Check ──")
    print(f"  Retrospective backtest rows: {total_retro}")
    if summary["reconstructed"] >= 10:
        print("  ✓ At least 10 restaurants reconstructed")
    else:
        print(f"  ⚠ Need 10+ restaurants, have {summary['reconstructed']}")
    print()


if __name__ == "__main__":
    main()
