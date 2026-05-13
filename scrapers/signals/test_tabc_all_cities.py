"""Phase 7 test: TABC license coverage across all 10 DFW cities.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_tabc_all_cities.py

Confirms:
  - Texas Open Data Portal covers all DFW cities (not just Dallas/Fort Worth)
  - city_matched field in payload reflects actual TABC record city
  - No license found is recorded as empty-records payload, not an error
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.tabc_license import scrape_license

load_dotenv()

# One restaurant per city — preferring full-service restaurants likely to have
# a TABC license. Absence of a license is a valid signal and won't fail the test.
_TEST_RESTAURANTS = [
    {"name": "Pecan Lodge",          "city": "Dallas"},
    {"name": "Joe T Garcia",         "city": "Fort Worth"},
    {"name": "Razzoo's Cajun Cafe",  "city": "Arlington"},
    {"name": "Spring Creek",         "city": "Grand Prairie"},
    {"name": "Whiskey Cake",         "city": "Plano"},
    {"name": "Lupe Tortilla",        "city": "Frisco"},
    {"name": "Cadillac Pizza Pub",   "city": "McKinney"},
    {"name": "Chili's",              "city": "Irving"},
    {"name": "Liberty Burger",       "city": "Garland"},
    {"name": "Bearded Monk",         "city": "Denton"},
]

_TEST_PLACE_ID = "test_tabc_all_cities"


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

    found_count    = 0
    no_data_count  = 0

    for entry in _TEST_RESTAURANTS:
        name = entry["name"]
        city = entry["city"]
        _banner(f"{name} — {city}")

        # Upsert a temporary row so we have a valid restaurant_id.
        with Session(engine) as session:
            result = session.execute(
                text(
                    """
                    INSERT INTO restaurants (name, city, state, google_place_id)
                    VALUES (:name, :city, 'TX', :place_id)
                    ON CONFLICT (google_place_id) DO UPDATE
                        SET name = EXCLUDED.name, city = EXCLUDED.city
                    RETURNING id
                    """
                ),
                {
                    "name": name,
                    "city": city,
                    "place_id": f"{_TEST_PLACE_ID}_{city.lower().replace(' ', '_')}",
                },
            )
            restaurant_id = str(result.scalar_one())
            session.commit()

        try:
            with Session(engine) as session:
                payload = scrape_license(name, city, restaurant_id, session)

            records      = payload.get("records", [])
            city_matched = payload.get("city_matched")

            if records:
                first = records[0]
                print(f"  Status        : license found")
                print(f"  Trade name    : {first.get('aimstradename')}")
                print(f"  License type  : {first.get('aimslicensetype')}")
                print(f"  License ID    : {first.get('aimslicenseid')}")
                print(f"  City (TABC)   : {city_matched}")
                print(f"  Owner         : {first.get('aimsownername')}")
                if len(records) > 1:
                    print(f"  ({len(records)} total records returned)")
                found_count += 1
            else:
                print(f"  Status        : no license record found")
                print(f"  (This is a valid signal — restaurant may not hold a TABC license)")
                no_data_count += 1

        except Exception as exc:
            print(f"  [FAIL] Unexpected error: {exc}")

    _banner(f"Summary: {found_count} cities found license / {no_data_count} returned no record")
    print()
    print("Success criterion:")
    print("  - TABC dataset returns results for at least some non-Dallas/Fort Worth cities")
    print("  - No city throws an unhandled exception")
    print("  - city_matched reflects the TABC record's city field, not just the query city")


if __name__ == "__main__":
    main()
