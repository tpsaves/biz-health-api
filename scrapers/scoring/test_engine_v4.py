"""Phase 6b integration test: run enhanced scoring against all onboarded restaurants.

Run inside the scrapers container:
    docker-compose exec scrapers python scoring/test_engine_v4.py

Unlike a C# unit test project, Python test scripts are plain executable modules.
We import the scoring engine directly and drive it against the live DB — the
equivalent of a C# integration test that spins up a real DbContext.
"""

import os
import sys

# Ensure the /scrapers directory is on sys.path so relative imports resolve.
# In C# this is handled by the project structure; in Python we manage it manually.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scoring.engine_v2 import compute_scores_v2

load_dotenv()


def _db_url() -> str:
    host     = os.environ.get("POSTGRES_HOST",     "db")
    port     = os.environ.get("POSTGRES_PORT",     "5432")
    db       = os.environ.get("POSTGRES_DB",       "bizhealth")
    user     = os.environ.get("POSTGRES_USER",     "admin")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60)


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} restaurant(s). Running Phase 6b enhanced scoring...\n")

    passed = 0
    failed = 0

    for restaurant_id, name in rows:
        _banner(f"{name}")
        try:
            with Session(engine) as session:
                scores = compute_scores_v2(str(restaurant_id), session)

            print(f"  overall_score          : {scores['overall_score']}")
            print(f"  review_velocity_score  : {scores['review_velocity_score']}")
            print(f"  rating_trend_score     : {scores['rating_trend_score']}")
            print(f"  operational_score      : {scores['operational_score']}")
            print()
            print(f"  -- Phase 6b velocity fields --")
            print(f"  days_since_last_review : {scores['days_since_last_review']}")
            print(f"  review_gap_alert       : {scores['review_gap_alert']}")
            print(f"  one_star_spike         : {scores['one_star_spike']}")
            print(f"  owner_response_rate    : {scores['owner_response_rate']}%")
            print(f"  monthly_volume_trend   : {scores['monthly_volume_trend']}")
            print(f"  seasonality_adjusted   : {scores['seasonality_adjusted']}")
            print(f"  comparison_method      : {scores['comparison_method']}")
            print()
            print(f"  -- Phase 6b rating fields --")
            print(f"  ninety_day_slope       : {scores['ninety_day_slope']}")
            print(f"  rating_deterioration   : {scores['rating_deterioration']}")
            print(f"  source_divergence      : {scores['source_divergence']}")
            print(f"  review_count_confidence: {scores['review_count_confidence']}")

            monthly = scores["score_factors"].get("monthlyReviewCounts", {})
            if monthly:
                print(f"\n  -- Monthly review counts (from Google API sample) --")
                for month_key in sorted(monthly.keys()):
                    bar = "█" * monthly[month_key]
                    print(f"  {month_key}: {monthly[month_key]:3d}  {bar}")

            # Verify new columns are in health_scores.
            with Session(engine) as session:
                row = session.execute(
                    text("""
                        SELECT review_gap_alert, one_star_spike, rating_deterioration,
                               source_divergence, ninety_day_slope, days_since_last_review,
                               owner_response_rate, monthly_volume_trend,
                               review_count_confidence, seasonality_adjusted, comparison_method
                        FROM health_scores
                        WHERE restaurant_id = :rid
                        ORDER BY scored_at DESC LIMIT 1
                    """),
                    {"rid": str(restaurant_id)},
                ).one_or_none()

            if row is None:
                print(f"\n  [FAIL] No health_scores row found after scoring!")
                failed += 1
                continue

            null_cols = [col for col, val in zip(row._fields, row) if val is None]
            if null_cols:
                # days_since_last_review can legitimately be None if no velocity_metrics yet.
                unexpected_nulls = [c for c in null_cols if c != "days_since_last_review"]
                if unexpected_nulls:
                    print(f"\n  [WARN] Unexpected NULL columns: {unexpected_nulls}")
                else:
                    print(f"\n  [OK] All Phase 6b columns populated (days_since_last may be null without fresh scrape).")
            else:
                print(f"\n  [OK] All Phase 6b columns populated.")

            passed += 1

        except Exception as exc:
            print(f"  [FAIL] {exc}")
            failed += 1

    _banner(f"Results: {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
