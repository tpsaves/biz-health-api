"""
Smoke test: fetch Google Places data for one DFW restaurant and verify it lands in the DB.

Run inside the scrapers container (from the project root):
    docker-compose exec scrapers python signals/test_google_places.py
    docker-compose exec scrapers python signals/test_google_places.py <place_id>
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Inside the container, docker-compose injects env vars directly.
# load_dotenv is a no-op when variables already exist, so this is safe.
load_dotenv()

# ── resolve place_id ───────────────────────────────────────────────────────────
# Default: Pecan Lodge, Deep Ellum, Dallas TX (well-known DFW BBQ restaurant)
DEFAULT_PLACE_ID = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"  # Pecan Lodge, Deep Ellum, Dallas TX
place_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PLACE_ID

# ── DB connection ──────────────────────────────────────────────────────────────
host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB", "bizhealth")
user = os.environ.get("POSTGRES_USER", "admin")
pwd  = os.environ["POSTGRES_PASSWORD"]

engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}")

print(f"Testing place_id: {place_id}")

with Session(engine) as session:
    # Upsert a test restaurant so we have a valid restaurant_id to reference.
    # ON CONFLICT keeps the existing row and returns its id either way.
    result = session.execute(
        text(
            """
            INSERT INTO restaurants (name, city, state, google_place_id)
            VALUES ('Test Restaurant', 'Dallas', 'TX', :place_id)
            ON CONFLICT (google_place_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        {"place_id": place_id},
    )
    restaurant_id = str(result.scalar_one())
    session.commit()
    print(f"Restaurant ID : {restaurant_id}")

    # Import directly (not via the package) because Python adds the script's own
    # directory (/app/signals) to sys.path when running `python signals/test_google_places.py`,
    # so `signals` as a package root is not visible here.
    from google_places import scrape_place

    payload = scrape_place(place_id, restaurant_id, session)

    # Confirm the row is in the DB
    row = session.execute(
        text(
            """
            SELECT id, scraped_at
            FROM raw_signals
            WHERE restaurant_id = :rid
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    result_data = payload.get("result", {})
    print(f"raw_signals row   : {row.id}")
    print(f"scraped_at        : {row.scraped_at}")
    print(f"Place name        : {result_data.get('name', '(not returned)')}")
    print(f"Rating            : {result_data.get('rating', '(not returned)')}")
    print(f"Total reviews     : {result_data.get('user_ratings_total', '(not returned)')}")
    print()
    print("OK — scraper wrote to DB successfully.")
