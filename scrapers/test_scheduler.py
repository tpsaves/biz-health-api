"""
End-to-end scheduler smoke test: runs one complete scrape-and-score cycle for
Pecan Lodge synchronously, then prints a full job summary.

This does NOT go through APScheduler — it calls the scraper and scoring functions
directly so the test is fast and deterministic. What it proves is that every scraper
and the scoring engine can be wired up and called in sequence without crashing,
which is exactly what the scheduler does in production.

Run inside the scrapers container:
    docker-compose exec scrapers python test_scheduler.py
"""
import os
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

load_dotenv()

host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB", "bizhealth")
user = os.environ.get("POSTGRES_USER", "admin")
pwd  = os.environ["POSTGRES_PASSWORD"]

# Package imports work here because Python adds /app (WORKDIR) to sys.path when
# running `python test_scheduler.py`, making signals/ and scoring/ importable as
# packages. The individual test scripts under signals/ use bare imports instead
# because they run as `python signals/test_google_places.py` and Python adds
# /app/signals to sys.path. Two different import modes, same underlying modules.
from signals.google_places import scrape_place
from signals.foursquare import scrape_venue
from signals.health_inspections import scrape_inspections
from signals.tabc_license import scrape_license
from signals.hours_monitor import scrape_hours
from scoring.engine_v2 import compute_scores_v2

engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}")

# ── Pecan Lodge test config ────────────────────────────────────────────────────
GOOGLE_PLACE_ID  = "ChIJGXYxd92YToYR7yV_BSMQ2Xk"
RESTAURANT_NAME  = "Pecan Lodge"
RESTAURANT_CITY  = "Dallas"
RESTAURANT_STATE = "TX"
FOURSQUARE_LAT   = 32.7828
FOURSQUARE_LNG   = -96.7834

print("=" * 70)
print("Scheduler smoke test — Pecan Lodge")
print("=" * 70)
print()

results: list[dict] = []

with Session(engine) as session:
    # Upsert restaurant — same pattern used by all individual test scripts
    row = session.execute(
        text(
            """
            INSERT INTO restaurants (name, city, state, google_place_id)
            VALUES (:name, :city, :state, :place_id)
            ON CONFLICT (google_place_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        {
            "name": RESTAURANT_NAME, "city": RESTAURANT_CITY,
            "state": RESTAURANT_STATE, "place_id": GOOGLE_PLACE_ID,
        },
    )
    restaurant_id = str(row.scalar_one())
    session.commit()
    print(f"Restaurant ID : {restaurant_id}")
    print()

    # ── Run all 5 scrapers + scoring in sequence ───────────────────────────────
    # Each step is wrapped in try/except so a single failure doesn't abort the
    # whole cycle — the same isolation the scheduler uses for production runs.
    steps = [
        ("google_places",     scrape_place,        (GOOGLE_PLACE_ID, restaurant_id, session)),
        ("foursquare",        scrape_venue,         (RESTAURANT_NAME, FOURSQUARE_LAT, FOURSQUARE_LNG, restaurant_id, session)),
        ("health_inspections", scrape_inspections,  (RESTAURANT_NAME, restaurant_id, session)),
        ("tabc_license",      scrape_license,       (RESTAURANT_NAME, RESTAURANT_CITY, restaurant_id, session)),
        ("hours_monitor",     scrape_hours,         (restaurant_id, session)),
        ("scoring_v2",        compute_scores_v2,    (restaurant_id, session)),
    ]

    for label, fn, args in steps:
        t0 = time.monotonic()
        status  = "OK"
        detail  = ""
        payload = None

        try:
            payload = fn(*args)
            elapsed = time.monotonic() - t0

            # Extract a human-readable detail line per scraper
            if label == "google_places":
                r = (payload or {}).get("result", {})
                detail = f"rating={r.get('rating')}  reviews={r.get('user_ratings_total')}"
            elif label == "foursquare":
                fsq_results = (payload or {}).get("search", {}).get("results", [])
                venue = fsq_results[0] if fsq_results else {}
                detail = f"fsq_place_id={venue.get('fsq_place_id', '—')}  rating_available={bool((payload or {}).get('details', {}).get('rating'))}"
            elif label == "health_inspections":
                records = (payload or {}).get("records", [])
                latest  = records[0] if records else {}
                detail  = f"records={len(records)}  latest_score={latest.get('score')}  date={str(latest.get('insp_date',''))[:10]}"
            elif label == "tabc_license":
                records = (payload or {}).get("records", [])
                first   = records[0] if records else {}
                detail  = f"records={len(records)}  type={first.get('aimslicensetype','—')}  owner={first.get('aimsownername','—')}"
            elif label == "hours_monitor":
                detail = f"days={payload.get('days_with_hours')}/7  completeness={payload.get('hours_completeness')}  open_now={payload.get('open_now')}"
            elif label == "scoring_v2":
                ops    = (payload or {}).get("operational_components", {})
                detail = (
                    f"overall={payload.get('overall_score')}  "
                    f"velocity={payload.get('review_velocity_score')}  "
                    f"rating={payload.get('rating_trend_score')}  "
                    f"ops={payload.get('operational_score')} "
                    f"(health={ops.get('health_inspection')} tabc={ops.get('tabc_license')} hours={ops.get('hours_completeness')})"
                )

        except Exception as exc:
            elapsed = time.monotonic() - t0
            status  = "FAILED"
            detail  = str(exc)

        results.append({"label": label, "status": status, "elapsed": elapsed, "detail": detail})

    print()

# ── Print summary table ────────────────────────────────────────────────────────
print("=" * 70)
print("Job Summary")
print("=" * 70)

all_ok = True
for r in results:
    icon = "✓" if r["status"] == "OK" else "✗"
    print(f"  {icon} {r['label']:<22} {r['status']:<8} {r['elapsed']:5.1f}s")
    print(f"      {r['detail']}")
    if r["status"] != "OK":
        all_ok = False

print()
print("=" * 70)

if all_ok:
    print("All steps completed successfully.")
else:
    print("One or more steps failed — see details above.")

print("=" * 70)
