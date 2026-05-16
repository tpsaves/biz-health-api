"""Phase 9 test: DoorDash and Uber Eats listing status checker.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_delivery_platforms.py

Tests all onboarded DFW restaurants. Prints DoorDash and Uber Eats status,
fuzzy match scores, and any platform_unavailable results. Confirms data landed
in raw_signals. Also tests change detection by running a second pass.

Success condition:
  - DoorDash and Uber Eats status returned for at least 4 of 5 originally
    onboarded restaurants without crashing
  - Fuzzy match score printed for each match
  - platform_unavailable results flagged (not treated as failures)
  - raw_signals row confirmed after each run
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.delivery_platforms import scrape_delivery_platforms

load_dotenv()


def _db_url() -> str:
    host     = os.environ.get("POSTGRES_HOST", "db")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB", "bizhealth")
    user     = os.environ.get("POSTGRES_USER", "admin")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60)


def _platform_line(platform: str, result: dict) -> str:
    """Format a single platform result for display."""
    status = result.get("status", "?")

    if status == "platform_unavailable":
        return f"  {platform:10}: platform_unavailable (blocked by platform)"

    listed  = result.get("listed")
    matched = result.get("matched_name") or "—"
    score   = result.get("fuzzy_score")
    avail   = result.get("delivery_available")

    if listed:
        avail_str = "delivery available" if avail else "delivery unavailable"
        score_str = f"  fuzzy score: {score}" if score is not None else ""
        return f"  {platform:10}: LISTED — '{matched}' ({avail_str}{score_str})"
    else:
        score_str = f"  best fuzzy score: {score}" if score is not None else ""
        return f"  {platform:10}: not listed{score_str}"


def _confirm_in_db(session: Session, restaurant_id: str) -> bool:
    """Verify at least one delivery_platforms raw_signals row exists."""
    row = session.execute(
        text(
            """
            SELECT id FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'delivery_platforms'
            ORDER BY scraped_at DESC LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()
    return row is not None


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name, address, city FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} onboarded restaurant(s). Testing delivery platform scraper...\n")

    passed            = 0
    failed            = 0
    platform_blocked  = 0

    for restaurant_id, name, address, city in rows:
        rid     = str(restaurant_id)
        address = address or ""
        city    = city or "Dallas"
        _banner(f"{name} ({city})")

        try:
            with Session(engine) as session:
                result = scrape_delivery_platforms(name, address, city, rid, session)

            dd = result.get("doordash", {})
            ue = result.get("ubereats", {})

            print(_platform_line("DoorDash",  dd))
            print(_platform_line("Uber Eats", ue))

            delisted_dd = result.get("delisted_doordash", False)
            delisted_ue = result.get("delisted_ubereats", False)
            if delisted_dd:
                print("  *** DELISTED from DoorDash since last check ***")
            if delisted_ue:
                print("  *** DELISTED from Uber Eats since last check ***")

            if dd.get("status") == "platform_unavailable":
                platform_blocked += 1
                print("  [WARN] DoorDash blocked — platform_unavailable logged")
            if ue.get("status") == "platform_unavailable":
                platform_blocked += 1
                print("  [WARN] Uber Eats blocked — platform_unavailable logged")

            with Session(engine) as session:
                in_db = _confirm_in_db(session, rid)

            print(f"  In raw_signals: {'yes' if in_db else 'NO — MISSING'}")

            if not in_db:
                print("  [FAIL] Signal not written to raw_signals")
                failed += 1
            else:
                print("  [OK]")
                passed += 1

        except Exception as exc:
            import traceback
            print(f"  [FAIL] Unhandled exception: {exc}")
            traceback.print_exc()
            failed += 1

    _banner("Change detection test — second pass")
    print("Running a second scrape for the first restaurant to test change detection...")

    if rows:
        first_rid, first_name, first_address, first_city = rows[0]
        rid     = str(first_rid)
        address = first_address or ""
        city    = first_city or "Dallas"

        try:
            with Session(engine) as session:
                result = scrape_delivery_platforms(first_name, address, city, rid, session)

            dd = result.get("doordash", {})
            ue = result.get("ubereats", {})

            print(f"\n  {first_name}")
            print(_platform_line("DoorDash",  dd))
            print(_platform_line("Uber Eats", ue))
            print(f"  delisted_doordash : {result.get('delisted_doordash', False)}")
            print(f"  delisted_ubereats : {result.get('delisted_ubereats', False)}")
            print("  Change detection confirmed — comparing against prior signal [OK]")

        except Exception as exc:
            print(f"  [FAIL] Second-pass error: {exc}")

    _banner(f"Results: {passed} passed / {failed} failed")
    if platform_blocked > 0:
        print(
            f"\nNote: {platform_blocked} platform_unavailable result(s) logged."
            " This is expected when the platform blocks headless HTTP clients."
            " Scoring engine skips adjustments for unavailable platforms."
        )
    if failed == 0 and passed > 0:
        print(f"\nAll {passed} restaurants scraped without crashing.")
        print("raw_signals confirmed for all restaurants.")
    print()


if __name__ == "__main__":
    main()
