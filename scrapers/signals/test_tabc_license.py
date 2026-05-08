"""
Smoke test: fetch TABC license records for Pecan Lodge and verify DB write.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_tabc_license.py
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

TRADE_NAME      = "Pecan Lodge"
CITY            = "Dallas"
GOOGLE_PLACE_ID = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"

print(f"Testing TABC license scraper for: {TRADE_NAME} ({CITY})")

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
    print(f"Restaurant ID    : {restaurant_id}")

    from tabc_license import scrape_license

    payload = scrape_license(TRADE_NAME, CITY, restaurant_id, session)

    row = session.execute(
        text(
            """
            SELECT id, scraped_at
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'tabc_license'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    records = payload["records"]
    print(f"raw_signals row  : {row.id}")
    print(f"scraped_at       : {row.scraped_at}")
    print(f"Records returned : {len(records)}")
    print()

    if records:
        print("── License Records ───────────────────────────────────────────────────")
        for r in records:
            print(f"  Trade name    : {r.get('aimstradename')}")
            print(f"  License type  : {r.get('aimslicensetype')}")
            print(f"  License ID    : {r.get('aimslicenseid')}")
            print(f"  Owner         : {r.get('aimsownername')}")
            print(f"  Address       : {r.get('locationaddress')}, {r.get('city')}, {r.get('state')}")
            print()
    else:
        print("  (no license records found)")
        print()

    print("OK — tabc_license scraper wrote to DB successfully.")
