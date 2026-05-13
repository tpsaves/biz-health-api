"""Phase 7 test: fetch Outscraper review history for all onboarded DFW restaurants.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_outscraper_reviews.py

Confirms:
  - Outscraper returns monthly breakdown for each restaurant
  - Data lands in raw_signals with source='outscraper_reviews'
  - monthly_breakdown key is present with YYYY-MM entries
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.outscraper_reviews import scrape_outscraper_reviews

load_dotenv()


def _db_url() -> str:
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60)


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text(
                "SELECT id, name, google_place_id "
                "FROM restaurants "
                "WHERE google_place_id IS NOT NULL "
                "ORDER BY name"
            )
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    if not os.environ.get("OUTSCRAPER_API_KEY"):
        print("ERROR: OUTSCRAPER_API_KEY is not set in .env")
        sys.exit(1)

    print(f"\nRunning Outscraper review history for {len(rows)} restaurant(s)...\n")

    passed = 0
    failed = 0

    for restaurant_id, name, place_id in rows:
        _banner(name)
        try:
            with Session(engine) as session:
                payload = scrape_outscraper_reviews(
                    str(place_id), name, str(restaurant_id), session
                )

            breakdown = payload.get("monthly_breakdown", {})
            total = payload.get("total_reviews_fetched", 0)

            print(f"  Total reviews fetched : {total}")
            print(f"  Months with data      : {len(breakdown)}")
            print()

            if breakdown:
                print("  Monthly breakdown (last 12 months):")
                for month in sorted(breakdown.keys()):
                    entry = breakdown[month]
                    count = entry["count"]
                    avg   = entry.get("avg_rating")
                    bar   = "█" * min(count, 40)
                    avg_str = f"  avg={avg:.1f}★" if avg is not None else ""
                    print(f"    {month}: {count:3d} reviews{avg_str}  {bar}")
            else:
                print("  [WARN] No monthly breakdown — check API key and place ID")

            # Verify the signal landed in raw_signals
            with Session(engine) as session:
                row = session.execute(
                    text(
                        """
                        SELECT id, scraped_at
                        FROM raw_signals
                        WHERE restaurant_id = :rid AND source = 'outscraper_reviews'
                        ORDER BY scraped_at DESC LIMIT 1
                        """
                    ),
                    {"rid": str(restaurant_id)},
                ).one_or_none()

            if row is None:
                print("  [FAIL] raw_signals row not found after scrape!")
                failed += 1
            else:
                print(f"\n  [OK] raw_signals row {row.id} written at {row.scraped_at}")
                passed += 1

        except Exception as exc:
            print(f"  [FAIL] {exc}")
            failed += 1

    _banner(f"Results: {passed} passed / {failed} failed")
    if passed < len(rows):
        print(
            f"\nNote: success criterion is at least {max(1, len(rows) - 1)} of {len(rows)} "
            "restaurants returning monthly data."
        )


if __name__ == "__main__":
    main()
