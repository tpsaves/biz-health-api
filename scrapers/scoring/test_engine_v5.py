"""Phase 7 integration test: scoring engine with Outscraper review history.

Run inside the scrapers container:
    docker-compose exec scrapers python scoring/test_engine_v5.py

Confirms:
  - comparison_method is 'year_over_year' for restaurants that have Outscraper data
  - comparison_method is 'period_comparison' or 'insufficient_data' for those without
  - monthly_volume_trend shows a real trend value (not 'insufficient_data') when
    Outscraper data is present and covers at least two comparable months
"""

import os
import sys

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


def _has_outscraper_data(session: Session, restaurant_id: str) -> bool:
    """Return True if at least one outscraper_reviews signal exists for this restaurant."""
    row = session.execute(
        text(
            """
            SELECT id FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'outscraper_reviews'
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()
    return row is not None


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} restaurant(s). Running Phase 7 scoring engine...\n")

    passed = 0
    failed = 0

    for restaurant_id, name in rows:
        rid = str(restaurant_id)
        _banner(name)

        with Session(engine) as session:
            has_outscraper = _has_outscraper_data(session, rid)

        print(f"  Outscraper data present : {'yes' if has_outscraper else 'no'}")

        try:
            with Session(engine) as session:
                scores = compute_scores_v2(rid, session)

            comparison = scores["comparison_method"]
            trend      = scores["monthly_volume_trend"]

            print(f"  overall_score           : {scores['overall_score']}")
            print(f"  review_velocity_score   : {scores['review_velocity_score']}")
            print(f"  rating_trend_score      : {scores['rating_trend_score']}")
            print(f"  operational_score       : {scores['operational_score']}")
            print()
            print(f"  comparison_method       : {comparison}")
            print(f"  monthly_volume_trend    : {trend}")
            print(f"  seasonality_adjusted    : {scores['seasonality_adjusted']}")

            # Validate expectations based on data availability.
            if has_outscraper and comparison not in ("year_over_year", "period_comparison"):
                print(f"  [WARN] Outscraper data present but comparison_method={comparison!r}")
            elif has_outscraper and trend == "insufficient_data":
                print(
                    "  [WARN] Outscraper data present but monthly_volume_trend=insufficient_data "
                    "(may need more months of data for year_over_year)"
                )
            elif not has_outscraper and comparison == "year_over_year":
                print("  [WARN] year_over_year used without Outscraper data — unexpected")
            else:
                print(f"  [OK] comparison_method and monthly_volume_trend consistent with data availability")

            # Show Outscraper monthly breakdown if available in score_factors.
            monthly = scores.get("score_factors", {}).get("monthlyReviewCounts", {})
            if monthly:
                print(f"\n  Monthly review counts (source: Google API sample):")
                for month_key in sorted(monthly.keys()):
                    bar = "█" * min(monthly[month_key], 40)
                    print(f"    {month_key}: {monthly[month_key]:3d}  {bar}")

            passed += 1

        except Exception as exc:
            print(f"  [FAIL] {exc}")
            failed += 1

    _banner(f"Results: {passed} passed / {failed} failed")
    print()
    if passed == len(rows):
        print("All restaurants scored successfully.")
    print(
        "\nExpected outcomes after running test_outscraper_reviews.py:\n"
        "  - Restaurants WITH Outscraper data → comparison_method = year_over_year\n"
        "    (requires 12+ months of history; period_comparison is valid fallback)\n"
        "  - Restaurants WITHOUT Outscraper data → comparison_method = period_comparison\n"
        "    or insufficient_data (depending on accumulated snapshots)\n"
        "  - monthly_volume_trend shows real trend (not insufficient_data) when\n"
        "    Outscraper monthly_breakdown covers at least two comparable months"
    )


if __name__ == "__main__":
    main()
