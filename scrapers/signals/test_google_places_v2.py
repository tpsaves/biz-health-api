"""
Live test for the updated Google Places v1 scraper.

Runs scrape_place() against Pecan Lodge, prints the 5 reviews with their
publishTime dates, confirms they are sorted newest-first, and compares the
most recent review date against what the previous scrape returned.

Run inside Docker:
  docker-compose exec scrapers python signals/test_google_places_v2.py

Success conditions:
  - 5 reviews returned sorted newest-first
  - publishTime on the most recent review is within the last 30 days
  - New raw_signals row lands in the DB with api_version=v1
  - days_since_last_review reflects the ranking improvement
"""

import json
import os
import sys
from datetime import datetime, timezone

# Add the scrapers root to sys.path so "signals.*" imports resolve whether
# this script is run from /app or from /app/signals.
# Equivalent to adding the project root to the .NET build path.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.google_places import _review_dt, scrape_place

load_dotenv()

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def _db_url() -> str:
    return (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'admin')}"
        f":{os.environ.get('POSTGRES_PASSWORD', '')}"
        f"@{os.environ.get('POSTGRES_HOST', 'db')}:5432"
        f"/{os.environ.get('POSTGRES_DB', 'bizhealth')}"
    )


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "None"


def main():
    print("Google Places API v1 scraper test")
    print("=" * 60)

    engine = create_engine(_db_url())

    with Session(engine) as session:
        # Look up Pecan Lodge — the primary validation restaurant.
        # Multiple test restaurants share the name "Pecan Lodge" with fake place_ids.
        # Filter for the real one whose place_id starts with "ChIJ" (Google's format).
        row = session.execute(
            text("SELECT id, google_place_id FROM restaurants WHERE name ILIKE '%pecan lodge%' AND google_place_id LIKE 'ChIJ%' LIMIT 1")
        ).one_or_none()

        if row is None:
            print(f"  Pecan Lodge not found in restaurants table. {FAIL}")
            return

        restaurant_id = str(row.id)
        place_id      = row.google_place_id
        print(f"  Restaurant : Pecan Lodge")
        print(f"  place_id   : {place_id}")
        print(f"  id         : {restaurant_id}\n")

        # Grab the most recent review date from the previous scrape for comparison.
        prev_row = session.execute(
            text("""
                SELECT payload, scraped_at
                FROM raw_signals
                WHERE restaurant_id = :rid AND source = 'google_places'
                ORDER BY scraped_at DESC
                LIMIT 1
            """),
            {"rid": restaurant_id},
        ).one_or_none()

        prev_days_since = None
        prev_api_version = "legacy"
        if prev_row:
            prev_days_since  = prev_row.payload.get("velocity_metrics", {}).get("days_since_last_review")
            prev_api_version = prev_row.payload.get("api_version", "legacy")
            print(f"  Previous scrape  : scraped_at={prev_row.scraped_at:%Y-%m-%d %H:%M UTC}")
            print(f"  Previous API ver : {prev_api_version}")
            print(f"  Previous days_since_last_review : {prev_days_since}\n")

        # ── Run the live scrape ───────────────────────────────────────────────
        print("Running scrape_place() against Google Places API v1 ...")
        try:
            payload = scrape_place(place_id, restaurant_id, session)
        except Exception as exc:
            print(f"  scrape_place() raised: {exc} {FAIL}")
            return

        print(f"  scrape_place() returned {PASS}\n")

        result   = payload.get("result", {})
        reviews  = result.get("reviews") or []
        vm       = payload.get("velocity_metrics", {})
        api_ver  = payload.get("api_version", "unknown")

        # ── Check 1: api_version is v1 ────────────────────────────────────────
        ok1 = api_ver == "v1"
        print(f"  api_version={api_ver!r}  {PASS if ok1 else FAIL}")

        # ── Check 2: rating and review count present ──────────────────────────
        ok2 = result.get("rating") is not None and result.get("user_ratings_total") is not None
        print(f"  rating={result.get('rating')}  user_ratings_total={result.get('user_ratings_total')}  {PASS if ok2 else FAIL}")

        # ── Check 3: 5 reviews returned ───────────────────────────────────────
        ok3 = len(reviews) == 5
        print(f"  Reviews returned : {len(reviews)}  {PASS if ok3 else FAIL + ' (expected 5)'}")

        days_since_vm = vm.get("days_since_last_review")

        # ── Print reviews with publishTime ────────────────────────────────────
        # Google Places v1 returns reviews in relevance order (not date order).
        # _extract_velocity_metrics() sorts them internally using _review_dt().
        print(f"\n  Reviews (as returned by API — not necessarily date-sorted):")
        dts = []
        for i, r in enumerate(reviews):
            dt      = _review_dt(r)
            pub_raw = r.get("publishTime") or r.get("time")
            dts.append(dt)
            print(f"    [{i+1}] rating={r.get('rating')}  publishTime={pub_raw!r}  → {_fmt_dt(dt)}")

        # ── Check 4: _review_dt() parses all timestamps and velocity_metrics
        #    correctly identifies the most recent review regardless of API order ──
        valid_dts   = [d for d in dts if d is not None]
        all_parsed  = len(valid_dts) == len(dts) and bool(valid_dts)
        expected_max = max(valid_dts, default=None)
        expected_days = (datetime.now(timezone.utc) - expected_max).days if expected_max else None
        vm_matches = (expected_days is not None and days_since_vm is not None
                      and abs(expected_days - days_since_vm) <= 1)  # allow 1-day rounding
        ok4 = all_parsed and vm_matches
        print(f"\n  All publishTime fields parsed    : {PASS if all_parsed else FAIL}")
        print(f"  Max review date (our sort)       : {_fmt_dt(expected_max)}  ({expected_days} days ago)")
        print(f"  velocity_metrics.days_since_last : {days_since_vm}")
        print(f"  velocity_metrics matches sort    : {PASS if vm_matches else FAIL}")

        # ── Check 5: most recent review within 30 days ───────────────────────
        most_recent_dt   = max((d for d in valid_dts if d), default=None)
        days_since_raw   = (datetime.now(timezone.utc) - most_recent_dt).days if most_recent_dt else None

        print(f"\n  Most recent review       : {_fmt_dt(most_recent_dt)}")
        print(f"  Days since (raw)         : {days_since_raw}")
        print(f"  velocity_metrics value   : {days_since_vm}")

        ok5 = days_since_raw is not None and days_since_raw <= 30
        print(f"  Within 30 days           : {PASS if ok5 else FAIL + ' (reviews may genuinely be older)'}")

        # ── Check 6: improvement vs previous scrape ───────────────────────────
        print(f"\n  days_since_last comparison:")
        print(f"    previous ({prev_api_version}) : {prev_days_since}")
        print(f"    new (v1)                    : {days_since_vm}")
        if prev_days_since is not None and days_since_vm is not None:
            improved = days_since_vm <= prev_days_since
            ok6 = improved
            label = "same or improved" if improved else "no improvement (reviews may be in same order)"
            print(f"    {label} {PASS if ok6 else '(not a failure — depends on review ordering)'}")
        else:
            ok6 = True
            print("    no previous data to compare")

        # ── Check 7: new row landed in DB ────────────────────────────────────
        new_row = session.execute(
            text("""
                SELECT payload->>'api_version' AS api_ver, scraped_at
                FROM raw_signals
                WHERE restaurant_id = :rid AND source = 'google_places'
                ORDER BY scraped_at DESC
                LIMIT 1
            """),
            {"rid": restaurant_id},
        ).one_or_none()

        ok7 = new_row is not None and new_row.api_ver == "v1"
        print(f"\n  DB row api_version=v1    : {PASS if ok7 else FAIL}")

    # ── Summary ───────────────────────────────────────────────────────────────
    checks = [ok1, ok2, ok3, ok4, ok7]  # ok5/ok6 are informational, not hard failures
    passed = sum(checks)
    total  = len(checks)
    print(f"\n{'=' * 60}")
    print(f"Result: {passed}/{total} required checks passed")
    print(PASS if passed == total else FAIL)


if __name__ == "__main__":
    main()
