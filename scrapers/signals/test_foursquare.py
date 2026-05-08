"""
Smoke test: fetch Foursquare data for one DFW restaurant and verify it lands in the DB.

Run inside the scrapers container (from the project root):
    docker-compose exec scrapers python signals/test_foursquare.py
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# load_dotenv is a no-op when variables are already injected by docker-compose;
# it only takes effect when running outside the container (e.g. locally with a .env file).
load_dotenv()

# ── DB connection ──────────────────────────────────────────────────────────────
host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB", "bizhealth")
user = os.environ.get("POSTGRES_USER", "admin")
pwd  = os.environ["POSTGRES_PASSWORD"]

engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}")

# Target restaurant for smoke testing
VENUE_NAME    = "Pecan Lodge"
VENUE_LAT     = 32.7828   # Deep Ellum, Dallas TX — coordinates replace the deprecated `near` text param
VENUE_LNG     = -96.7834
GOOGLE_PLACE_ID = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"

print(f"Testing Foursquare scraper for: {VENUE_NAME} ({VENUE_LAT}, {VENUE_LNG})")

with Session(engine) as session:
    # Upsert the restaurant so we have a valid restaurant_id foreign key.
    # ON CONFLICT ... DO UPDATE SET is the PostgreSQL equivalent of EF Core's AddOrUpdate.
    result = session.execute(
        text(
            """
            INSERT INTO restaurants (name, city, state, google_place_id)
            VALUES ('Pecan Lodge', 'Dallas', 'TX', :place_id)
            ON CONFLICT (google_place_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        {"place_id": GOOGLE_PLACE_ID},
    )
    restaurant_id = str(result.scalar_one())
    session.commit()
    print(f"Restaurant ID : {restaurant_id}")

    # Import directly (not via the package) because when Python runs
    # `python signals/test_foursquare.py`, it adds /app/signals to sys.path —
    # the same directory the script lives in — so foursquare.py is importable directly.
    from foursquare import scrape_venue

    payload = scrape_venue(VENUE_NAME, VENUE_LAT, VENUE_LNG, restaurant_id, session)

    # Confirm the row landed in raw_signals
    row = session.execute(
        text(
            """
            SELECT id, scraped_at
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'foursquare'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    details = payload.get("details", {})
    stats   = details.get("stats", {})
    print(f"raw_signals row   : {row.id}")
    print(f"scraped_at        : {row.scraped_at}")
    print(f"Venue name        : {details.get('name', '(not returned)')}")
    print(f"Rating (0-10)     : {details.get('rating', '(not returned)')}")
    print(f"Total ratings     : {stats.get('total_ratings', '(not returned)')}")
    print()
    print("OK — Foursquare scraper wrote to DB successfully.")
