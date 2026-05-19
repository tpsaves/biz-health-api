"""Test: Closed Restaurant Finder.

Runs the closed restaurant finder for all 10 DFW cities, prints counts per
source, shows 10 sample records, and confirms data landed in the DB.

Usage (from inside the scrapers container):
    python backtesting/test_closed_finder.py
"""

import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Allow import from the scrapers root (e.g. signals.*, scoring.*).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.closed_restaurant_finder import run_closed_restaurant_finder

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


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
    print("CLOSED RESTAURANT FINDER — Test Run")
    print("=" * 60)

    with Session(engine) as session:
        summary = run_closed_restaurant_finder(session)

    print("\n── Source Counts ──")
    print(f"  Google Places (CLOSED_PERMANENTLY): {summary['google_count']}")
    print(f"  TABC Cancelled/Expired:             {summary['tabc_count']}")
    print(f"  Yelp Permanently Closed:            {summary['yelp_count']}")
    print(f"  Inspection Inference:               {summary['inferred_count']}")
    print(f"  Texas SOS / Franchise Tax:          {summary['sos_count']}")
    print(f"  Raw total (pre-dedup):              {summary['raw_total']}")
    print(f"  Unique (post-dedup):                {summary['unique_total']}")
    print(f"  Inserted to DB:                     {summary['inserted']}")

    # Pull 10 samples from the DB.
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT name, city, closure_date, closure_date_estimated, closure_source
                FROM closed_restaurants
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
        ).fetchall()

        total_in_db = session.execute(
            text("SELECT count(*) FROM closed_restaurants")
        ).scalar()

    print(f"\n── Sample Records (10 most recent) ──")
    for row in rows:
        est = " (estimated)" if row.closure_date_estimated else ""
        print(
            f"  {row.name:<40}  {row.city:<15}  "
            f"{str(row.closure_date or 'unknown'):>12}{est}  [{row.closure_source}]"
        )

    print(f"\n── DB Check ──")
    print(f"  Total rows in closed_restaurants: {total_in_db}")

    if total_in_db >= 50:
        print("  ✓ Target met: 50+ closed restaurants in DB")
    else:
        print(f"  ⚠ Below target: need 50+, have {total_in_db}")

    print()


if __name__ == "__main__":
    main()
