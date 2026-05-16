"""Phase 9 integration test: scoring engine with delivery platform signals.

Run inside the scrapers container:
    docker-compose exec scrapers python scoring/test_engine_v7.py

Confirms:
  - operational_score adjusts correctly based on delivery platform listing status
  - doordash_listed, ubereats_listed, delivery_status populated in health_scores
  - delivery_platform_loss flag triggers correctly when a restaurant drops off a platform
  - DoorDash and Uber Eats status printed for each restaurant

Python note for C# developers:
  sys.path.insert(0, ...) makes the parent directory importable — equivalent to
  adding a project reference in C# .csproj so sibling assemblies resolve correctly.
  In Python there is no project file; you manage the module search path manually.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scoring.engine_v2 import compute_scores_v2

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


def _platform_status_line(label: str, listed) -> str:
    if listed is True:
        return f"  {label:12}: Listed (active)"
    elif listed is False:
        return f"  {label:12}: Not listed"
    else:
        return f"  {label:12}: Unknown (platform unavailable)"


def _confirm_delivery_columns(session: Session, restaurant_id: str) -> dict:
    """Read the latest health_scores row and return Phase 9 delivery fields."""
    row = session.execute(
        text(
            """
            SELECT doordash_listed, ubereats_listed,
                   delivery_platform_count, delivery_status,
                   delivery_platform_loss, operational_score
            FROM health_scores
            WHERE restaurant_id = :rid
            ORDER BY scored_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    # row._mapping gives a dict-like view of the row — equivalent to reading
    # individual column properties from an EF Core entity in C#.
    return dict(row._mapping) if row else {}


def _delivery_signal_exists(session: Session, restaurant_id: str) -> bool:
    """Check whether a delivery_platforms raw_signal exists for this restaurant."""
    row = session.execute(
        text(
            """
            SELECT 1 FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'delivery_platforms'
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()
    return row is not None


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} restaurant(s). Running Phase 9 scoring engine...\n")

    passed = 0
    failed = 0

    for restaurant_id, name in rows:
        rid = str(restaurant_id)
        _banner(name)

        try:
            with Session(engine) as session:
                scores = compute_scores_v2(rid, session)

            print(f"  overall_score           : {scores['overall_score']}")
            print(f"  review_velocity_score   : {scores['review_velocity_score']}")
            print(f"  rating_trend_score      : {scores['rating_trend_score']}")
            print(f"  operational_score       : {scores['operational_score']}")
            print(f"  financial_risk_score    : {scores['financial_risk_score']}")

            print()
            print(_platform_status_line("DoorDash",  scores.get("doordash_listed")))
            print(_platform_status_line("Uber Eats", scores.get("ubereats_listed")))
            print(f"  delivery_status         : {scores.get('delivery_status', 'unknown')}")
            print(f"  delivery_platform_count : {scores.get('delivery_platform_count', '—')}")

            if scores.get("delivery_platform_loss"):
                print("\n  *** DELIVERY PLATFORM LOSS ACTIVE ***")
                print("  Restaurant was listed but is now offline on one or more platforms.")

            # Verify new columns landed in health_scores.
            with Session(engine) as session:
                db_row = _confirm_delivery_columns(session, rid)
                signal_exists = _delivery_signal_exists(session, rid)

            if db_row:
                print(f"\n  DB doordash_listed      : {db_row.get('doordash_listed')}")
                print(f"  DB ubereats_listed      : {db_row.get('ubereats_listed')}")
                print(f"  DB delivery_status      : {db_row.get('delivery_status')}")
                print(f"  DB delivery_platform_count: {db_row.get('delivery_platform_count')}")
                print(f"  DB delivery_platform_loss : {db_row.get('delivery_platform_loss')}")
                print(f"  DB operational_score    : {db_row.get('operational_score')}")
                print(f"  delivery_platforms signal in raw_signals: {'yes' if signal_exists else 'no (run test_delivery_platforms.py first)'}")
                print("  [OK] Phase 9 columns present in health_scores")
                passed += 1
            else:
                print("  [FAIL] No health_scores row found after scoring")
                failed += 1

        except Exception as exc:
            import traceback
            print(f"  [FAIL] {exc}")
            traceback.print_exc()
            failed += 1

    _banner(f"Results: {passed} passed / {failed} failed")
    print()
    if passed == len(rows):
        print("All restaurants scored with Phase 9 delivery platform signals.")

    print(
        "\nWeight distribution (unchanged from Phase 8):\n"
        "  review_velocity_score : 20%\n"
        "  rating_trend_score    : 30%\n"
        "  operational_score     : 30%  ← delivery platform adjustments apply here\n"
        "  financial_risk_score  : 20%\n"
        "\nDelivery platform operational_score adjustments:\n"
        "  Listed on both platforms      : +5\n"
        "  Listed on one platform only   : no change\n"
        "  Never listed (conclusive)     : -10\n"
        "  Was listed, now offline       : -20  (sets delivery_platform_loss=True)\n"
        "  Platform data unavailable     : no adjustment"
    )
    print()


if __name__ == "__main__":
    main()
