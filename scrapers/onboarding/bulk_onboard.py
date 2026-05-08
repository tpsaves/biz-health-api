"""
Bulk restaurant onboarding from a CSV file or an in-memory list of dicts.

# Python CSV handling vs C#
# ------------------------------------------------------------------
# C# uses CsvHelper (NuGet) or TextFieldParser from Microsoft.VisualBasic.
# Python's stdlib `csv` module handles this with no external package.
#
# csv.DictReader consumes the first row as column headers automatically and
# yields each subsequent row as a dict[str, str] — equivalent to CsvHelper's
# GetRecords<T>(), but without a strongly-typed POCO class. Columns are
# accessed by string key (row["name"]) rather than by property; a missing
# column raises KeyError at runtime, not a compile-time error.
#
# File handling:
#   with open(path, newline="", encoding="utf-8") as f:
#       reader = csv.DictReader(f)
#       for row in reader: ...          # lazy — one dict per iteration
#
# The `with` block is Python's `using` statement: the file closes on exit
# whether or not an exception is raised, without an explicit try/finally.
# `newline=""` is required by the csv docs to prevent double-newline issues
# on Windows (equivalent to opening with FileMode.Open and no StreamReader
# TextWriter auto-normalization).

# Dynamic job scheduling vs C#
# ------------------------------------------------------------------
# In Hangfire you'd call RecurringJob.AddOrUpdate(restaurantId, ...) per
# restaurant, creating one persisted recurring job per row in the DB.
#
# The APScheduler design here is different: a single `run_daily_scrape` job
# in main.py calls _get_restaurants() at execution time and iterates all rows.
# Onboarding a new restaurant (inserting a DB row) is all that's needed —
# no new scheduler job registration required. The scheduler is data-driven
# rather than job-per-entity.
#
# Trade-off: fewer scheduler entries and simpler bookkeeping, but the job
# function must be stateless and resilient to rows being added/removed
# between runs. Hangfire's per-entity model gives finer-grained retry and
# monitoring at the cost of a larger job table.
"""
import csv
import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.google_places import scrape_place
from signals.foursquare import scrape_venue
from signals.health_inspections import scrape_inspections
from signals.tabc_license import scrape_license
from signals.hours_monitor import scrape_hours
from scoring.engine_v2 import compute_scores_v2

load_dotenv()
logger = logging.getLogger(__name__)

_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def lookup_place(name: str, address: str, city: str) -> tuple[str, float, float]:
    """Google Places Text Search → (place_id, lat, lng).

    Takes the highest-confidence result for the given name + address query.
    Raises RuntimeError if the API returns no results or a non-OK status.
    """
    api_key = os.environ["GOOGLE_PLACES_API_KEY"]
    query = f"{name} {address} {city} TX"

    resp = httpx.get(
        _TEXT_SEARCH_URL,
        params={"query": query, "key": api_key},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status != "OK":
        raise RuntimeError(f"Text Search returned status={status!r} for {name!r}")

    result = data["results"][0]
    place_id = result["place_id"]
    lat = result["geometry"]["location"]["lat"]
    lng = result["geometry"]["location"]["lng"]
    return place_id, lat, lng


def _step(label: str, fn, args: tuple, steps: list) -> object:
    """Run one scraper step, record timing and status, return payload or None."""
    t0 = time.monotonic()
    try:
        payload = fn(*args)
        steps.append({
            "step": label,
            "status": "OK",
            "elapsed": time.monotonic() - t0,
            "detail": _detail(label, payload),
        })
        return payload
    except Exception as exc:
        steps.append({
            "step": label,
            "status": "FAILED",
            "elapsed": time.monotonic() - t0,
            "detail": str(exc),
        })
        logger.error("Onboarding — %s FAILED: %s", label, exc)
        return None


def _detail(label: str, payload: object) -> str:
    """Extract a human-readable summary line from a scraper payload."""
    if not payload:
        return ""
    if label == "google_places":
        res = payload.get("result", {})
        return f"rating={res.get('rating')} reviews={res.get('user_ratings_total')}"
    if label == "foursquare":
        fsq = payload.get("search", {}).get("results", [])
        venue = fsq[0] if fsq else {}
        return f"fsq_place_id={venue.get('fsq_place_id', '—')}"
    if label == "health_inspections":
        recs = payload.get("records", [])
        latest = recs[0] if recs else {}
        return f"records={len(recs)} latest_score={latest.get('score', '—')}"
    if label == "tabc_license":
        recs = payload.get("records", [])
        first = recs[0] if recs else {}
        return f"records={len(recs)} type={first.get('aimslicensetype', '—')}"
    if label == "hours_monitor":
        return f"days={payload.get('days_with_hours')}/7 completeness={payload.get('hours_completeness')}"
    if label == "scoring_v2":
        return (
            f"overall={payload.get('overall_score')} "
            f"velocity={payload.get('review_velocity_score')} "
            f"rating={payload.get('rating_trend_score')} "
            f"ops={payload.get('operational_score')}"
        )
    return ""


def onboard_restaurants(rows: list[dict]) -> list[dict]:
    """Onboard a list of restaurant dicts and run all scrapers + scoring for each.

    Each dict must have at minimum: name, address, city, zip.
    Returns a list of result dicts — one per restaurant — with step-level detail.
    """
    engine = _build_engine()
    results = []

    for row in rows:
        name    = row["name"].strip()
        address = row.get("address", "").strip()
        city    = row.get("city", "Dallas").strip()
        zip_    = row.get("zip", "").strip()

        r: dict = {"name": name, "steps": [], "error": None}

        # Step 1: look up Google Place ID and coordinates via Text Search.
        # We separate this from scrape_place because Text Search returns
        # geometry (lat/lng) that we need for Foursquare, while the existing
        # scrape_place function uses the Details API (place_id → rich data).
        t0 = time.monotonic()
        try:
            place_id, lat, lng = lookup_place(name, address, city)
            r["steps"].append({
                "step": "place_lookup",
                "status": "OK",
                "elapsed": time.monotonic() - t0,
                "detail": f"place_id={place_id} lat={lat:.4f} lng={lng:.4f}",
            })
        except Exception as exc:
            r["steps"].append({
                "step": "place_lookup",
                "status": "FAILED",
                "elapsed": time.monotonic() - t0,
                "detail": str(exc),
            })
            r["error"] = str(exc)
            results.append(r)
            continue

        with Session(engine) as session:
            # Upsert restaurant — ON CONFLICT makes this idempotent on re-run.
            # Equivalent to EF Core's AddOrUpdate, but SQL-native; no change
            # tracker is involved — we write the merge logic explicitly.
            row_result = session.execute(
                text("""
                    INSERT INTO restaurants (name, address, city, state, zip, google_place_id)
                    VALUES (:name, :address, :city, 'TX', :zip, :place_id)
                    ON CONFLICT (google_place_id) DO UPDATE
                        SET name       = EXCLUDED.name,
                            address    = EXCLUDED.address,
                            city       = EXCLUDED.city,
                            zip        = EXCLUDED.zip,
                            updated_at = NOW()
                    RETURNING id
                """),
                {"name": name, "address": address, "city": city,
                 "zip": zip_, "place_id": place_id},
            )
            rid = str(row_result.scalar_one())
            session.commit()
            r["restaurant_id"] = rid

            # Steps 2–7: run all scrapers then score.
            # One failure per step is logged and recorded but does not abort
            # the remaining steps — same isolation pattern as main.py's
            # run_daily_scrape(). Each step calls session.commit() internally.
            scrape_steps = [
                ("google_places",      scrape_place,       (place_id, rid, session)),
                ("foursquare",         scrape_venue,       (name, lat, lng, rid, session)),
                ("health_inspections", scrape_inspections, (name, rid, session)),
                ("tabc_license",       scrape_license,     (name, city, rid, session)),
                ("hours_monitor",      scrape_hours,       (rid, session)),
                ("scoring_v2",         compute_scores_v2,  (rid, session)),
            ]

            for label, fn, args in scrape_steps:
                _step(label, fn, args, r["steps"])

        results.append(r)

    return results


def onboard_from_csv(csv_path: str) -> list[dict]:
    """Read restaurants from a CSV file and onboard each one.

    CSV must have a header row with at minimum: name, address, city, zip.
    Extra columns are ignored.
    """
    path = Path(csv_path)

    # `with open(...) as f:` closes the file on block exit — Python's `using`.
    # newline="" is required by the csv spec so the reader handles \r\n itself.
    # encoding="utf-8" matches the file written by most editors on Windows/Mac.
    with open(path, newline="", encoding="utf-8") as f:
        # DictReader reads the first row as column headers automatically.
        # Equivalent to CsvHelper's reader.GetRecords<T>() but yields
        # dict[str, str] rather than a typed POCO — duck-typed access by key.
        reader = csv.DictReader(f)
        rows = list(reader)   # materialize before the file closes

    logger.info("Loaded %d restaurants from %s", len(rows), path.name)
    return onboard_restaurants(rows)
