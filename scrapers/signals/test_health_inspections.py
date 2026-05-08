"""
Smoke test: fetch Dallas health inspection records for Pecan Lodge and verify DB write.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_health_inspections.py
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

INSPECTION_NAME  = "Pecan Lodge"
GOOGLE_PLACE_ID  = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"

print(f"Testing health inspections scraper for: {INSPECTION_NAME}")

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
    print(f"Restaurant ID     : {restaurant_id}")

    from health_inspections import scrape_inspections

    payload = scrape_inspections(INSPECTION_NAME, restaurant_id, session)

    row = session.execute(
        text(
            """
            SELECT id, scraped_at
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'health_inspections'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    records = payload["records"]
    latest  = records[0]
    print(f"raw_signals row   : {row.id}")
    print(f"scraped_at        : {row.scraped_at}")
    print(f"Records returned  : {len(records)}")
    print()
    print("── Latest Inspection ─────────────────────────────────────────────────")
    print(f"  Establishment : {latest.get('program_identifier')}")
    print(f"  Date          : {latest.get('insp_date', '')[:10]}")
    print(f"  Score         : {latest.get('score')}")

    # Count non-null violation descriptions
    violations = [
        latest.get(f"violation{i}_description")
        for i in range(1, 16)
        if latest.get(f"violation{i}_description")
    ]
    print(f"  Violations    : {len(violations)}")
    for v in violations:
        print(f"    - {v}")

    print()
    print("OK — health_inspections scraper wrote to DB successfully.")
