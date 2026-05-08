"""
Smoke test: extract hours from the stored Google Places signal for Pecan Lodge
and verify the hours_monitor row lands in raw_signals.

Requires a google_places signal already in the DB — run test_google_places.py first.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_hours_monitor.py
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

load_dotenv()

host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB", "bizhealth")
user = os.environ.get("POSTGRES_USER", "admin")
pwd  = os.environ["POSTGRES_PASSWORD"]

engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}")

GOOGLE_PLACE_ID = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"

print("Testing hours_monitor for: Pecan Lodge")

with Session(engine) as session:
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
    print(f"Restaurant ID      : {restaurant_id}")

    from hours_monitor import scrape_hours

    payload = scrape_hours(restaurant_id, session)

    row = session.execute(
        text(
            """
            SELECT id, scraped_at
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'hours_monitor'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    print(f"raw_signals row    : {row.id}")
    print(f"scraped_at         : {row.scraped_at}")
    print()
    print("── Hours Analysis ────────────────────────────────────────────────────")
    print(f"  Days with hours  : {payload['days_with_hours']} / 7")
    print(f"  Completeness     : {payload['hours_completeness']} / 100")
    print(f"  Open now         : {payload['open_now']}")
    print()

    if payload["weekday_text"]:
        print("── Schedule ──────────────────────────────────────────────────────────")
        for line in payload["weekday_text"]:
            print(f"  {line}")
        print()

    print("OK — hours_monitor wrote to DB successfully.")
