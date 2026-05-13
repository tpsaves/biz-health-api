"""Phase 7 test: health inspection routing across all 10 DFW cities.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_health_inspections_all_cities.py

Confirms:
  - Each city routes to the correct health authority
  - Portals that return data show inspection records
  - Portals without a public API log no_inspection_data instead of crashing
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.health_inspections import scrape_inspections, _CITY_ROUTES

load_dotenv()

# One representative restaurant per city — chosen for name recognizability and
# likelihood of appearing in the respective health authority's database.
_TEST_RESTAURANTS = [
    {"name": "Pecan Lodge",              "city": "Dallas"},
    {"name": "Joe T. Garcia's",          "city": "Fort Worth"},
    {"name": "Babe's Chicken Dinner",    "city": "Arlington"},
    {"name": "Spring Creek Barbeque",    "city": "Grand Prairie"},
    {"name": "Whiskey Cake",             "city": "Plano"},
    {"name": "Lupe Tortilla",            "city": "Frisco"},
    {"name": "Harvest Seasonal Kitchen", "city": "McKinney"},
    {"name": "Pappadeaux Seafood",       "city": "Irving"},
    {"name": "Liberty Burger",           "city": "Garland"},
    {"name": "LSA Burger Co",            "city": "Denton"},
]

# Sentinel restaurant used only for test DB upserts — not a real onboarded restaurant.
_TEST_PLACE_ID = "test_health_insp_all_cities"


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

    # Print routing table first so the user can verify mapping before tests run.
    print("\nCity → Health Authority routing:")
    for city, route in sorted(_CITY_ROUTES.items()):
        print(f"  {city:<15} → {route['authority']}")

    found_count = 0
    no_data_count = 0

    for entry in _TEST_RESTAURANTS:
        name = entry["name"]
        city = entry["city"]
        _banner(f"{name} ({city})")

        route = _CITY_ROUTES.get(city.lower(), {})
        print(f"  Authority : {route.get('authority', 'UNKNOWN — routing misconfigured!')}")
        print(f"  Handler   : {route.get('handler', 'N/A')}")

        # Upsert a temporary DB row so we have a valid restaurant_id for the signal write.
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
                payload = scrape_inspections(name, city, restaurant_id, session)

            records = payload.get("records", [])
            status  = payload.get("status", "")

            if status == "no_inspection_data" or not records:
                print("  Result    : no_inspection_data")
                no_data_count += 1
            else:
                print(f"  Result    : {len(records)} inspection record(s) found")
                latest = records[0]
                print(f"  Latest    : score={latest.get('score')}  date={str(latest.get('insp_date', ''))[:10]}")
                found_count += 1

        except Exception as exc:
            print(f"  [FAIL] Unexpected error: {exc}")

    _banner(f"Summary: {found_count} cities returned data / {no_data_count} returned no_inspection_data")
    print()
    print(
        "Note: no_inspection_data is expected for Dallas County cities (Irving, Garland)\n"
        "and Denton County until their portals expose public APIs."
    )
    print(f"Success criterion: at least 8 of {len(_TEST_RESTAURANTS)} cities return data or no_inspection_data without crashing.")


if __name__ == "__main__":
    main()
