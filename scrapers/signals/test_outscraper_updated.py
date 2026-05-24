"""Verify updated Outscraper configuration and backfill status.

Run inside Docker:
    docker-compose exec scrapers python signals/test_outscraper_updated.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.outscraper_reviews import _MAX_REVIEWS
from signals.outscraper_quota import MONTHLY_CAP, monthly_summary

load_dotenv()

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_BACKFILL_CUTOFF = datetime(2026, 3, 25, tzinfo=timezone.utc)

_passed = 0
_failed = 0


def check(label: str, got, expected) -> None:
    global _passed, _failed
    if got == expected:
        print(f"  {PASS}  {label}")
        _passed += 1
    else:
        print(f"  {FAIL}  {label}")
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")
        _failed += 1


def _db_url() -> str:
    host     = os.environ.get("POSTGRES_HOST", "db")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB", "bizhealth")
    user     = os.environ.get("POSTGRES_USER", "admin")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


# ── Static config checks ─────────────────────────────────────────────────────

print("\n── Outscraper configuration ──")

check("_MAX_REVIEWS = 70",   _MAX_REVIEWS, 70)
check("MONTHLY_CAP = 15000", MONTHLY_CAP,  15_000)

projected = 70 * 107 * 2
check("projected monthly usage = 14,980", projected, 14_980)

projected_cost = round(projected * 0.003, 2)
check("projected monthly cost = $44.94",  projected_cost, 44.94)


# ── Live DB checks ───────────────────────────────────────────────────────────

print("\n── Live DB checks ──")

engine = create_engine(_db_url())

with Session(engine) as session:
    summary = monthly_summary(session)

print(f"  Month:                  {summary['month']}")
print(f"  Records used (regular): {summary['records_used']:,} / {summary['cap']:,}")
print(f"  Estimated cost:         {summary['estimated_cost']}")
print(f"  Remaining:              {summary['records_remaining']:,}")
print(f"  Backfill records used:  {summary['backfill_records_used']:,}")
print(f"  Projected monthly:      {summary['projected_monthly']:,}")

check("cap in summary = 15,000",          summary["cap"],               15_000)
check("projected_monthly in summary",     summary["projected_monthly"], 14_980)
check("backfill_records_used key exists", "backfill_records_used" in summary, True)


# ── Backfill status ──────────────────────────────────────────────────────────

print("\n── Backfill status (March 25, 2026 cutoff) ──")

with Session(engine) as session:
    restaurants = session.execute(
        text(
            "SELECT id, name FROM restaurants "
            "WHERE google_place_id IS NOT NULL ORDER BY name"
        )
    ).fetchall()

    needs_backfill = []
    has_history    = []

    for r in restaurants:
        rid      = str(r.id)
        earliest = session.execute(
            text(
                "SELECT MIN(scraped_at) FROM raw_signals "
                "WHERE restaurant_id = CAST(:rid AS uuid) AND source = 'outscraper_reviews'"
            ),
            {"rid": rid},
        ).scalar()

        if earliest is None or earliest.replace(tzinfo=timezone.utc) > _BACKFILL_CUTOFF:
            needs_backfill.append(r.name)
        else:
            has_history.append(r.name)

total = len(restaurants)
print(f"  Total restaurants:       {total}")
print(f"  Have pre-March-25 data:  {len(has_history)}")
print(f"  Still need backfill:     {len(needs_backfill)}")

if needs_backfill:
    print(f"\n  Restaurants pending backfill ({len(needs_backfill)}):")
    for name in needs_backfill[:20]:
        print(f"    • {name}")
    if len(needs_backfill) > 20:
        print(f"    … and {len(needs_backfill) - 20} more")
    print("\n  NOTE: Backfill job runs automatically 10s after scrapers container starts.")
else:
    print("\n  All restaurants have history from March 25, 2026 — backfill complete.")


# ── Summary ──────────────────────────────────────────────────────────────────

total_checks = _passed + _failed
print(f"\n{'─'*50}")
print(f"  {_passed}/{total_checks} checks passed", end="")
if _failed:
    print(f"  ({_failed} failed)")
    sys.exit(1)
else:
    print("  — all good")
print()
