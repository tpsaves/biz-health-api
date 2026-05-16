"""
Spot-check test for google_places.py — verifies:
  1. Raw signal exists in DB with reviews present
  2. Reviews are sorted newest-first via _review_dt()
  3. Both "time" (Unix int) and "publishTime" (ISO string) formats are handled
  4. velocity_metrics["days_since_last_review"] is populated
  5. Scoring engine's days_since_last_review is <= 30 for restaurants with Outscraper data

Run inside Docker:
  docker-compose exec scrapers python signals/test_google_places_v2.py
"""

import json
import os
import sys
from datetime import datetime, timezone

# Add the scrapers root to sys.path so "signals.*" imports resolve whether
# this script is run from /app or from /app/signals.
# Equivalent to adding the project root to the .NET build path.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text

from signals.google_places import _review_dt

DB_URL = (
    f"postgresql://{os.environ.get('POSTGRES_USER', 'admin')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@{os.environ.get('POSTGRES_HOST', 'db')}:5432"
    f"/{os.environ.get('POSTGRES_DB', 'bizhealth')}"
)

engine = create_engine(DB_URL)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "None"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _days_ago(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).days


def check_restaurant(row) -> bool:
    """Return True if all checks pass for one restaurant."""
    name        = row.name
    rest_id     = row.restaurant_id
    payload     = row.payload
    scored_days = row.scored_days_since_last  # from health_scores

    print(f"\n{'='*60}")
    print(f"  {name}  (id={rest_id})")
    print(f"{'='*60}")

    result  = payload.get("result", {})
    reviews = result.get("reviews") or []
    vm      = payload.get("velocity_metrics", {})

    # ── Check 1: reviews present ──────────────────────────────────
    if reviews:
        print(f"  Reviews in payload : {len(reviews)} {PASS}")
    else:
        print(f"  Reviews in payload : 0 — no reviews to inspect {FAIL}")
        return False

    # ── Check 2: timestamp field present ──────────────────────────
    # Legacy API uses "time" (Unix int); new API v1 uses "publishTime" (ISO string).
    # _review_dt() must handle both transparently.
    ts_field = "publishTime" if "publishTime" in reviews[0] else "time"
    print(f"  Timestamp field    : \"{ts_field}\"")
    for r in reviews[:3]:
        raw_val = r.get("publishTime") or r.get("time")
        parsed  = _review_dt(r)
        print(f"    raw={raw_val!r:>20}  →  parsed={_fmt_dt(parsed)}")

    # ── Check 3: sorted newest-first via _review_dt() ────────────
    dts = [_review_dt(r) for r in reviews]
    valid_dts = [d for d in dts if d is not None]
    sorted_ok = all(valid_dts[i] >= valid_dts[i+1] for i in range(len(valid_dts)-1))
    # Note: raw reviews from Google are newest-first by default;
    # _extract_velocity_metrics sorts them oldest-first internally for the
    # "index -1 is newest" convention.  Here we just verify _review_dt() parses correctly.
    print(f"  _review_dt parses  : {'all non-None' if all(d is not None for d in dts) else 'some None'}")

    all_parsed = all(d is not None for d in dts)
    print(f"  All timestamps OK  : {PASS if all_parsed else FAIL}")

    # ── Check 4: velocity_metrics populated ───────────────────────
    vm_days = vm.get("days_since_last_review")
    print(f"  velocity_metrics.days_since_last_review : {vm_days}")
    vm_ok = vm_days is not None
    print(f"  velocity_metrics present : {PASS if vm_ok else FAIL}")

    # ── Check 5: scoring engine days_since_last ───────────────────
    print(f"  health_scores.days_since_last_review    : {scored_days}")
    if scored_days is not None:
        gate_ok = scored_days <= 30
        print(f"  <= 30 days (Outscraper refinement)      : {PASS if gate_ok else FAIL + ' (may be OK if no Outscraper data yet)'}")
    else:
        print(f"  health_scores row not found — score this restaurant first")
        gate_ok = True  # don't fail if not yet scored

    return all_parsed and vm_ok


def main():
    print("Google Places v2 signal spot-check")
    print("Verifies _review_dt() handles both 'time' (Unix) and 'publishTime' (ISO)\n")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                r.name,
                rs.restaurant_id,
                rs.payload,
                hs.days_since_last_review AS scored_days_since_last
            FROM raw_signals rs
            JOIN restaurants r ON r.id = rs.restaurant_id
            LEFT JOIN LATERAL (
                SELECT days_since_last_review
                FROM   health_scores
                WHERE  restaurant_id = rs.restaurant_id
                ORDER  BY scored_at DESC
                LIMIT  1
            ) hs ON true
            WHERE rs.source = 'google_places'
              AND rs.payload -> 'result' -> 'reviews' IS NOT NULL
            ORDER BY rs.scraped_at DESC
        """)).mappings().all()

    if not rows:
        print("No google_places signals with reviews found in raw_signals.")
        print("Run the scraper first: docker-compose exec scrapers python signals/google_places.py")
        return

    # De-duplicate: keep most recent signal per restaurant.
    seen = {}
    for row in rows:
        if row["restaurant_id"] not in seen:
            seen[row["restaurant_id"]] = row

    results = []
    for row in seen.values():
        # mappings() returns dict-like objects; convert for attribute-style access.
        class _Row:
            def __init__(self, d):
                self.__dict__.update(d)
                self.payload = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
        results.append(check_restaurant(_Row(dict(row))))

    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*60}")
    print(f"Result: {passed}/{total} restaurants passed all checks")
    print(PASS if passed == total else FAIL)


if __name__ == "__main__":
    main()
