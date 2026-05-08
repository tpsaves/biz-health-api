"""
Smoke test: run the scoring engine against Pecan Lodge and confirm the row lands in health_scores.

Requires at least one google_places row in raw_signals (run test_google_places.py first).
A foursquare row is optional — the engine degrades gracefully without it.

Run inside the scrapers container (from the project root):
    docker-compose exec scrapers python scoring/test_engine.py
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# load_dotenv is a no-op when docker-compose has already injected the env vars.
load_dotenv()

# ── DB connection ──────────────────────────────────────────────────────────────
host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB", "bizhealth")
user = os.environ.get("POSTGRES_USER", "admin")
pwd  = os.environ["POSTGRES_PASSWORD"]

engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}")

GOOGLE_PLACE_ID = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"  # Pecan Lodge, Deep Ellum, Dallas TX

print("Running scoring engine for: Pecan Lodge")

with Session(engine) as session:
    # Resolve the restaurant_id — upsert so this test is safe to run in any order.
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

    # Check what raw_signals exist before scoring so we can report on them.
    sources = session.execute(
        text(
            """
            SELECT DISTINCT source
            FROM raw_signals
            WHERE restaurant_id = :rid
            """
        ),
        {"rid": restaurant_id},
    ).fetchall()
    # .fetchall() returns a list of Row objects; [r[0] for r in rows] unpacks each single-column row.
    # In C# you'd do rows.Select(r => r.Source).ToList().
    available_sources = [r[0] for r in sources]
    print(f"Available signals : {available_sources or ['(none — run scrapers first)']}")
    print()

    # Import directly because Python adds /app/scoring to sys.path when running
    # `python scoring/test_engine.py`, making engine.py importable without a package prefix.
    from engine import compute_scores

    scores = compute_scores(restaurant_id, session)

    print("── Computed Scores ───────────────────────────────────────────────────")
    print(f"  review_velocity_score : {scores['review_velocity_score']:>3}  (weight 40%)")
    print(f"  rating_trend_score    : {scores['rating_trend_score']:>3}  (weight 60%)")
    print(f"  overall_score         : {scores['overall_score']:>3}")
    print()

    # Confirm the row landed in health_scores
    row = session.execute(
        text(
            """
            SELECT id, review_velocity_score, rating_trend_score, overall_score, scored_at
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
    print(f"  overall_score         : {row.overall_score}")
    print()
    print("OK — scoring engine wrote to health_scores successfully.")
