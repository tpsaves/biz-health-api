"""Manual import tool for closed restaurant records.

Reads a CSV file and imports records directly into the closed_restaurants table.
Use this to supplement automated sources with manually researched closures found
via Google searches, local news articles, Yelp, or other manual investigation.

CSV columns (all optional except 'name'):
  name          Business name (required)
  address       Street address
  city          City (defaults to 'Dallas' if omitted)
  zip           ZIP code
  closure_date  Known closure date in YYYY-MM-DD format (leave blank if unknown)
  source        Free-text label, e.g. "manual_news", "yelp_manual" (defaults to 'manual')

Usage (from inside the scrapers container):
    python backtesting/import_closed_restaurants.py /backtesting_results/my_closures.csv

Or with a custom path:
    python backtesting/import_closed_restaurants.py /path/to/file.csv --dry-run
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"name"}
_OPTIONAL_COLUMNS = {"address", "city", "zip", "closure_date", "source"}
_ALL_COLUMNS = _REQUIRED_COLUMNS | _OPTIONAL_COLUMNS


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _parse_date(value: str) -> tuple[str | None, bool]:
    """Return (iso_date_str, is_estimated). Empty string → (None, None)."""
    value = value.strip()
    if not value:
        return None, None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat(), False
        except ValueError:
            pass
    logger.warning("Could not parse date %r — treating as unknown", value)
    return None, None


def load_csv(path: str) -> list[dict]:
    """Read and validate the CSV file. Returns a list of normalised record dicts."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV file not found: {path}")

    records = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}

        missing = _REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(f"CSV is missing required column(s): {missing}")

        unknown = headers - _ALL_COLUMNS
        if unknown:
            logger.warning("CSV contains unrecognised column(s) %s — they will be ignored", unknown)

        for i, row in enumerate(reader, start=2):  # start=2 because row 1 is the header
            name = row.get("name", "").strip()
            if not name:
                logger.warning("Row %d: empty 'name' — skipping", i)
                continue

            closure_date, is_estimated = _parse_date(row.get("closure_date", ""))

            records.append({
                "name":                   name,
                "address":                row.get("address", "").strip() or None,
                "city":                   row.get("city", "Dallas").strip() or "Dallas",
                "zip":                    row.get("zip", "").strip() or None,
                "closure_date":           closure_date,
                "closure_date_estimated": is_estimated,
                "closure_source":         row.get("source", "manual").strip() or "manual",
            })

    return records


def import_records(records: list[dict], session: Session, dry_run: bool = False) -> dict:
    """Insert records into closed_restaurants, skipping existing name+city pairs."""
    inserted = 0
    skipped = 0

    for rec in records:
        exists = session.execute(
            text(
                "SELECT 1 FROM closed_restaurants "
                "WHERE lower(name) = lower(:name) AND lower(city) = lower(:city) LIMIT 1"
            ),
            {"name": rec["name"], "city": rec["city"]},
        ).one_or_none()

        if exists:
            logger.info("Skip (already in DB): %s — %s", rec["name"], rec["city"])
            skipped += 1
            continue

        if dry_run:
            logger.info("[dry-run] Would insert: %s — %s", rec["name"], rec["city"])
            inserted += 1
            continue

        session.execute(
            text(
                """
                INSERT INTO closed_restaurants
                  (name, address, city, zip, closure_date,
                   closure_date_estimated, closure_source)
                VALUES
                  (:name, :address, :city, :zip, :closure_date,
                   :closure_date_estimated, :closure_source)
                """
            ),
            rec,
        )
        logger.info("Inserted: %s — %s", rec["name"], rec["city"])
        inserted += 1

    if not dry_run:
        session.commit()

    return {"inserted": inserted, "skipped": skipped, "total": len(records)}


def main():
    parser = argparse.ArgumentParser(description="Import closed restaurants from CSV")
    parser.add_argument("csv_path", help="Path to CSV file")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate CSV without writing to the database",
    )
    args = parser.parse_args()

    print(f"\nLoading: {args.csv_path}")
    records = load_csv(args.csv_path)
    print(f"Parsed {len(records)} record(s) from CSV")

    if args.dry_run:
        print("-- DRY RUN: no DB writes --")

    engine = _build_engine()
    with Session(engine) as session:
        result = import_records(records, session, dry_run=args.dry_run)

    print(f"\n── Import Summary ──")
    print(f"  Total rows in CSV:  {result['total']}")
    print(f"  Inserted:           {result['inserted']}")
    print(f"  Skipped (dupe):     {result['skipped']}")
    if args.dry_run:
        print("  (dry-run — no rows written)")
    print()


if __name__ == "__main__":
    main()
