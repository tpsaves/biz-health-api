"""Cohort cleaner — removes non-restaurant businesses from backtest_cohort.

Reads all prospective cohort entries, classifies each business using
restaurant_classifier.py, and removes NON_RESTAURANT entries from the
backtest_cohort table. Also removes the matching restaurants row when the
business was onboarded solely for backtesting (no Outscraper review history,
indicating it was never part of the core scoring pipeline).

Usage (from inside the scrapers container):
    python backtesting/clean_cohort.py
    python backtesting/clean_cohort.py --dry-run
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


def clean_cohort(session: Session, dry_run: bool = False) -> dict:
    """Classify all prospective cohort entries and remove non-restaurants."""
    rows = session.execute(
        text("""
            SELECT bc.id            AS cohort_id,
                   bc.restaurant_id,
                   r.name,
                   r.city,
                   r.google_place_id,
                   bc.baseline_risk_band,
                   bc.baseline_score
            FROM backtest_cohort bc
            JOIN restaurants r ON bc.restaurant_id = r.id
            WHERE bc.cohort_type = 'prospective'
            ORDER BY r.name
        """)
    ).fetchall()

    kept: list[str] = []
    removed: list[dict] = []
    by_band: dict[str, dict] = defaultdict(lambda: {"kept": 0, "removed": 0})

    for row in rows:
        result = classify(row.name, row.google_place_id)
        band = row.baseline_risk_band

        if result["classification"] == "NON_RESTAURANT":
            removed.append({
                "name":          row.name,
                "city":          row.city,
                "band":          band,
                "score":         row.baseline_score,
                "reason":        result["reason"],
                "cohort_id":     str(row.cohort_id),
                "restaurant_id": str(row.restaurant_id),
            })
            by_band[band]["removed"] += 1
            logger.info("[clean] REMOVE [%s] %s — %s", band, row.name, result["reason"])
        else:
            kept.append(row.name)
            by_band[band]["kept"] += 1
            logger.debug("[clean] KEEP   [%s] %s", band, row.name)

    if not dry_run:
        for entry in removed:
            # Always remove from backtest_cohort.
            session.execute(
                text("DELETE FROM backtest_cohort WHERE id = CAST(:id AS uuid)"),
                {"id": entry["cohort_id"]},
            )

            # Remove from restaurants only if the business was onboarded solely
            # for backtesting — identified by having no Outscraper review data
            # (core scoring-pipeline restaurants are scraped weekly by Outscraper).
            has_outscraper = session.execute(
                text(
                    "SELECT 1 FROM raw_signals "
                    "WHERE restaurant_id = CAST(:rid AS uuid) AND source = 'outscraper_reviews' "
                    "LIMIT 1"
                ),
                {"rid": entry["restaurant_id"]},
            ).one_or_none()

            has_other_cohort = session.execute(
                text(
                    "SELECT 1 FROM backtest_cohort "
                    "WHERE restaurant_id = CAST(:rid AS uuid) LIMIT 1"
                ),
                {"rid": entry["restaurant_id"]},
            ).one_or_none()

            if not has_outscraper and not has_other_cohort:
                session.execute(
                    text("DELETE FROM raw_signals WHERE restaurant_id = CAST(:rid AS uuid)"),
                    {"rid": entry["restaurant_id"]},
                )
                session.execute(
                    text("DELETE FROM health_scores WHERE restaurant_id = CAST(:rid AS uuid)"),
                    {"rid": entry["restaurant_id"]},
                )
                session.execute(
                    text("DELETE FROM restaurants WHERE id = CAST(:id AS uuid)"),
                    {"id": entry["restaurant_id"]},
                )
                logger.info("[clean] Purged restaurant record: %s", entry["name"])

        session.commit()

    return {
        "total_processed": len(rows),
        "kept":            len(kept),
        "removed":         len(removed),
        "by_band":         {k: dict(v) for k, v in by_band.items()},
        "removed_list":    removed,
    }


def main():
    parser = argparse.ArgumentParser(description="Remove non-restaurant businesses from backtest_cohort")
    parser.add_argument("--dry-run", action="store_true", help="Classify without deleting")
    args = parser.parse_args()

    engine = _build_engine()
    with Session(engine) as session:
        result = clean_cohort(session, dry_run=args.dry_run)

    print(f"\n── Cohort Cleaning Summary ──")
    print(f"  Total processed:     {result['total_processed']}")
    print(f"  Kept (restaurants):  {result['kept']}")
    print(f"  Removed (non-rest):  {result['removed']}")
    if args.dry_run:
        print("  (dry-run — no rows deleted)")

    print(f"\n  By risk band:")
    for band in ("low", "moderate", "elevated", "high"):
        counts = result["by_band"].get(band, {"kept": 0, "removed": 0})
        print(f"    {band:10s}  kept={counts['kept']:3d}  removed={counts['removed']:3d}")

    print(f"\n  Removed businesses:")
    for entry in sorted(result["removed_list"], key=lambda x: (x["band"], x["name"])):
        print(f"    [{entry['band']:8s} {entry['score']:3d}] {entry['name']} ({entry['city']}) — {entry['reason']}")
    print()


if __name__ == "__main__":
    main()
