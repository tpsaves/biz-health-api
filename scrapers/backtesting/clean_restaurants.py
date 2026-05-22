"""Full restaurant table cleaner — removes non-restaurant businesses.

Uses keyword matching (no API calls) as the primary classification method.
Businesses with no keyword match are flagged as UNKNOWN for manual review.
Optionally calls Google Places API for UNKNOWN entries when --check-google is passed.

Deletion order respects FK constraints (no cascade on outscraper_usage/backtest_cohort):
  1. outscraper_usage   (no cascade)
  2. backtest_cohort    (no cascade)
  3. restaurants        (cascades to raw_signals and health_scores)

Usage (from inside scrapers container):
    python backtesting/clean_restaurants.py [--dry-run] [--check-google]
"""

import argparse
import logging
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from backtesting.restaurant_classifier import classify


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def clean_restaurants(session: Session, dry_run: bool = False, check_google: bool = False) -> dict:
    """Classify all restaurants and remove confirmed non-restaurants."""
    rows = session.execute(
        text("SELECT id, name, city, google_place_id FROM restaurants ORDER BY name")
    ).fetchall()

    to_remove: list[dict] = []
    to_flag:   list[dict] = []
    kept_count = 0
    by_keyword: dict[str, int] = defaultdict(int)

    for row in rows:
        place_id = row.google_place_id if check_google else None
        result = classify(row.name, place_id)

        if result["classification"] == "NON_RESTAURANT":
            to_remove.append({
                "id":     str(row.id),
                "name":   row.name,
                "city":   row.city,
                "reason": result["reason"],
            })
            # Track which keyword triggered the removal
            if "name contains" in result["reason"]:
                kw = result["reason"].split("'")[1]
                by_keyword[kw] += 1
            logger.info("[clean] REMOVE %s (%s) — %s", row.name, row.city, result["reason"])
        elif result["classification"] == "UNKNOWN":
            to_flag.append({
                "id":     str(row.id),
                "name":   row.name,
                "city":   row.city,
                "reason": result["reason"],
            })
        else:
            kept_count += 1

    if not dry_run:
        for entry in to_remove:
            rid = entry["id"]
            # Delete from tables without cascade first
            session.execute(
                text("DELETE FROM outscraper_usage WHERE restaurant_id = CAST(:rid AS uuid)"),
                {"rid": rid},
            )
            session.execute(
                text("DELETE FROM backtest_cohort WHERE restaurant_id = CAST(:rid AS uuid)"),
                {"rid": rid},
            )
            # Deleting from restaurants cascades to raw_signals and health_scores
            session.execute(
                text("DELETE FROM restaurants WHERE id = CAST(:rid AS uuid)"),
                {"rid": rid},
            )
        session.commit()

    return {
        "total_processed": len(rows),
        "removed":         len(to_remove),
        "flagged_unknown": len(to_flag),
        "kept":            kept_count,
        "by_keyword":      dict(by_keyword),
        "removed_list":    to_remove,
        "flagged_list":    to_flag,
    }


def main():
    parser = argparse.ArgumentParser(description="Remove non-restaurant businesses from restaurants table")
    parser.add_argument("--dry-run",      action="store_true", help="Classify without deleting")
    parser.add_argument("--check-google", action="store_true", help="Call Google Places API for UNKNOWN entries")
    args = parser.parse_args()

    engine = _build_engine()
    with Session(engine) as session:
        result = clean_restaurants(session, dry_run=args.dry_run, check_google=args.check_google)

    print(f"\n── Restaurant Table Cleaning Summary ──")
    print(f"  Total processed:       {result['total_processed']}")
    print(f"  Removed (non-rest):    {result['removed']}")
    print(f"  Flagged (unknown):     {result['flagged_unknown']}")
    print(f"  Kept (confirmed rest): {result['kept']}")
    if args.dry_run:
        print("  (dry-run — no rows deleted)")

    if result["by_keyword"]:
        print(f"\n  Removed by keyword trigger:")
        for kw, count in sorted(result["by_keyword"].items(), key=lambda x: -x[1]):
            print(f"    '{kw}': {count} restaurant(s)")

    print(f"\n  Removed businesses:")
    for entry in sorted(result["removed_list"], key=lambda x: x["name"]):
        print(f"    {entry['name']} ({entry['city']}) — {entry['reason']}")

    if result["flagged_list"]:
        print(f"\n  Flagged for review (UNKNOWN — could not classify):")
        for entry in sorted(result["flagged_list"], key=lambda x: x["name"]):
            print(f"    {entry['name']} ({entry['city']}) — {entry['reason']}")

    print()


if __name__ == "__main__":
    main()
