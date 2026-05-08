"""
Smoke test: run the v2 scoring engine against Pecan Lodge and print a full score breakdown.

Requires all signals to already be in the DB:
    docker-compose exec scrapers python signals/test_google_places.py
    docker-compose exec scrapers python signals/test_health_inspections.py
    docker-compose exec scrapers python signals/test_tabc_license.py
    docker-compose exec scrapers python signals/test_hours_monitor.py
    (foursquare is optional — engine degrades gracefully without rating data)

Run inside the scrapers container:
    docker-compose exec scrapers python scoring/test_engine_v2.py
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

print("Running v2 scoring engine for: Pecan Lodge")
print()

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
    print(f"Restaurant ID         : {restaurant_id}")

    # Show which signals are available before scoring
    sources = [
        r[0]
        for r in session.execute(
            text("SELECT DISTINCT source FROM raw_signals WHERE restaurant_id = :rid"),
            {"rid": restaurant_id},
        ).fetchall()
    ]
    print(f"Available signals     : {sources}")
    print()

    from engine_v2 import compute_scores_v2

    scores = compute_scores_v2(restaurant_id, session)
    ops    = scores["operational_components"]

    print("── Computed Scores ───────────────────────────────────────────────────")
    print(f"  review_velocity_score : {scores['review_velocity_score']:>3}  (weight 25%)")
    print(f"  rating_trend_score    : {scores['rating_trend_score']:>3}  (weight 25%)")
    print(f"  operational_score     : {scores['operational_score']:>3}  (weight 50%)")
    print(f"    ├── health_inspection  : {ops['health_inspection']:>3}  (50% of operational)")
    print(f"    ├── tabc_license       : {ops['tabc_license']:>3}  (30% of operational)")
    print(f"    └── hours_completeness : {ops['hours_completeness']:>3}  (20% of operational)")
    print(f"  staffing_score        : n/a  (Phase 3 — job postings signal not yet built)")
    print(f"  overall_score         : {scores['overall_score']:>3}")
    print()

    # Confirm the row landed in health_scores
    row = session.execute(
        text(
            """
            SELECT id, review_velocity_score, rating_trend_score,
                   operational_score, staffing_score, overall_score, scored_at
            FROM health_scores
            WHERE restaurant_id = :rid
            ORDER BY scored_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one()

    print("── DB Confirmation ───────────────────────────────────────────────────")
    print(f"  health_scores row     : {row.id}")
    print(f"  scored_at             : {row.scored_at}")
    print(f"  review_velocity_score : {row.review_velocity_score}")
    print(f"  rating_trend_score    : {row.rating_trend_score}")
    print(f"  operational_score     : {row.operational_score}")
    print(f"  staffing_score        : {row.staffing_score}")
    print(f"  overall_score         : {row.overall_score}")
    print()
    print("OK — v2 scoring engine wrote to health_scores successfully.")
