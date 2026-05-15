"""Phase 8 test: business personal property tax scraper.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_property_tax.py

Tests the 5 onboarded DFW restaurants and confirms:
  - Scraper runs without crashing for each restaurant
  - Results or no_data_available are clearly printed
  - Raw signal lands in raw_signals table

Most DFW CADs do not expose public REST APIs, so no_data_available is the
expected result for all restaurants in the current implementation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.property_tax import scrape_property_tax

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


def _confirm_in_db(session: Session, restaurant_id: str) -> bool:
    row = session.execute(
        text(
            """
            SELECT id FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'property_tax'
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
            text("SELECT id, name, city FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} restaurant(s). Testing property tax scraper...\n")

    passed = 0
    failed = 0

    for restaurant_id, name, city in rows:
        rid  = str(restaurant_id)
        city = city or "Dallas"
        _banner(f"{name} ({city})")

        try:
            with Session(engine) as session:
                result = scrape_property_tax(name, city, rid, session)

            status      = result.get("status", "?")
            cad         = result.get("cad", "?")
            delinquent  = result.get("delinquent", False)
            years       = result.get("years_delinquent", 0)
            amount      = result.get("delinquent_amount")
            fuzzy_score = result.get("_fuzzy_match_score")

            print(f"  CAD             : {cad}")
            print(f"  Status          : {status}")

            if status == "no_data_available":
                print("  [INFO] no_data_available — CAD does not expose a public API")
                print("         This is expected and treated as neutral (no score penalty)")
            else:
                print(f"  Delinquent      : {delinquent}")
                if delinquent:
                    print(f"  Years delinquent: {years}")
                    if amount is not None:
                        print(f"  Delinquent amt  : ${amount:,.2f}")
                if fuzzy_score is not None:
                    print(f"  Fuzzy match     : {fuzzy_score}")

            with Session(engine) as session:
                in_db = _confirm_in_db(session, rid)
            print(f"  In raw_signals  : {'yes' if in_db else 'NO — MISSING'}")

            if not in_db:
                print("  [FAIL] Signal not written to raw_signals")
                failed += 1
            else:
                print("  [OK]")
                passed += 1

        except Exception as exc:
            print(f"  [FAIL] Crashed: {exc}")
            failed += 1

    _banner(f"Results: {passed} passed / {failed} failed")
    if failed == 0:
        print("All restaurants processed without crashing.")
    print(
        "\nNote: no_data_available is a valid success outcome for this scraper.\n"
        "When a CAD publishes a public API, implement _fetch_*_cad() in property_tax.py\n"
        "and this test will automatically pick up real data."
    )
    print()


if __name__ == "__main__":
    main()
