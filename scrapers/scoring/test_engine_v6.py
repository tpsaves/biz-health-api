"""Phase 8 integration test: scoring engine with financial risk signals.

Run inside the scrapers container:
    docker-compose exec scrapers python scoring/test_engine_v6.py

Confirms:
  - financial_risk_score is computed and present in health_scores for all restaurants
  - All four component scores print without error
  - Active financial risk flags (sba_default, tax_delinquent) are displayed
  - New columns (financial_risk_score, sba_default, etc.) are populated in health_scores
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


def _confirm_financial_columns(session: Session, restaurant_id: str) -> dict:
    """Read the latest health_scores row and return financial fields."""
    row = session.execute(
        text(
            """
            SELECT financial_risk_score, sba_default, repeated_sba_borrowing,
                   tax_delinquent, sba_loan_count, sba_latest_status,
                   sba_latest_amount, tax_delinquency_years
            FROM health_scores
            WHERE restaurant_id = :rid
            ORDER BY scored_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    if row is None:
        return {}

    # row._mapping gives a dict-like view — equivalent to reading individual properties
    # in C# after EF Core materializes the row into an entity.
    return dict(row._mapping)


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} restaurant(s). Running Phase 8 scoring engine...\n")

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

            # Financial risk flags
            fin_flags = []
            if scores.get("sba_default"):          fin_flags.append("sba_default")
            if scores.get("repeated_sba_borrowing"): fin_flags.append("repeated_sba_borrowing")
            if scores.get("tax_delinquent"):        fin_flags.append("tax_delinquent")

            print(f"\n  SBA loan count          : {scores.get('sba_loan_count', 0)}")
            print(f"  SBA latest status       : {scores.get('sba_latest_status', 'none_found')}")
            if scores.get("sba_latest_amount"):
                print(f"  SBA latest amount       : ${scores['sba_latest_amount']:,.0f}")
            print(f"  Tax delinquency years   : {scores.get('tax_delinquency_years', 0)}")

            if fin_flags:
                print(f"\n  *** FINANCIAL FLAGS ACTIVE: {', '.join(fin_flags)} ***")
            else:
                print(f"\n  No financial risk flags")

            # Verify DB columns are populated
            with Session(engine) as session:
                db_row = _confirm_financial_columns(session, rid)

            if db_row:
                print(f"\n  DB financial_risk_score : {db_row.get('financial_risk_score')}")
                print(f"  DB sba_default          : {db_row.get('sba_default')}")
                print(f"  DB tax_delinquent       : {db_row.get('tax_delinquent')}")
                print(f"  DB sba_latest_status    : {db_row.get('sba_latest_status')}")
                print("  [OK] financial columns present in health_scores")
                passed += 1
            else:
                print("  [FAIL] No health_scores row found after scoring")
                failed += 1

        except Exception as exc:
            print(f"  [FAIL] {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    _banner(f"Results: {passed} passed / {failed} failed")
    print()
    if passed == len(rows):
        print("All restaurants scored with Phase 8 financial risk signals.")
    print(
        "\nWeight distribution used:\n"
        "  review_velocity_score : 20%\n"
        "  rating_trend_score    : 30%\n"
        "  operational_score     : 30%\n"
        "  financial_risk_score  : 20%\n"
        "\nScore caps:\n"
        "  sba_default=True        → overall_score capped at 45\n"
        "  tax_delinquent (2+ yrs) → overall_score capped at 55"
    )
    print()


if __name__ == "__main__":
    main()
