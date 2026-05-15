"""Phase 8 test: SBA loan history scraper.

Run inside the scrapers container:
    docker-compose exec scrapers python signals/test_sba_loans.py

Tests the 5 onboarded DFW restaurants plus 2 known restaurant chains
that are likely to have SBA loan history.

Success condition:
  - Scraper returns results or none_found for all restaurants without crashing
  - Raw signal lands in raw_signals table
  - Fuzzy match score is printed for any matched loans
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.sba_loans import scrape_sba_loans

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
    """Verify a raw_signals row was written for this restaurant."""
    row = session.execute(
        text(
            """
            SELECT id FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'sba_loans'
            ORDER BY scraped_at DESC LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()
    return row is not None


def _print_loan(loan: dict) -> None:
    status  = loan.get("LoanStatus", "")
    amount  = loan.get("GrossApproval")
    date    = (loan.get("ApprovalDate") or "")[:10]
    score   = loan.get("_fuzzy_match_score", 0)
    chargeoff = loan.get("ChargeOffDate")
    print(f"    Borrower  : {loan.get('BorrowerName')}")
    print(f"    Status    : {status}  (fuzzy score: {score})")
    print(f"    Amount    : ${float(amount or 0):,.0f}  Approved: {date}")
    if chargeoff:
        print(f"    Charged off: {chargeoff}")


def main() -> None:
    engine = create_engine(_db_url())

    # ── Onboarded DFW restaurants ──────────────────────────────────────────
    with Session(engine) as session:
        rows = session.execute(
            text("SELECT id, name, zip FROM restaurants ORDER BY name")
        ).fetchall()

    if not rows:
        print("No restaurants found. Run bulk onboarding first.")
        sys.exit(1)

    print(f"\nFound {len(rows)} onboarded restaurant(s). Testing SBA loan scraper...\n")

    passed = 0
    failed = 0

    for restaurant_id, name, zip_code in rows:
        rid      = str(restaurant_id)
        zip_code = zip_code or "75201"  # fallback if zip is null
        _banner(f"{name} (zip: {zip_code})")

        try:
            with Session(engine) as session:
                result = scrape_sba_loans(name, zip_code, rid, session)

            risk_flag  = result.get("risk_flag", "?")
            loan_count = result.get("loan_count", 0)
            status     = result.get("sba_latest_status", "?")
            print(f"  Risk flag       : {risk_flag}")
            print(f"  Loan count      : {loan_count}")
            print(f"  Latest status   : {status}")

            if loan_count > 0:
                print(f"  Matched loans:")
                for loan in result.get("matched_loans", [])[:5]:
                    _print_loan(loan)

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
            print(f"  [FAIL] {exc}")
            failed += 1

    # ── Known chains likely to have SBA history ────────────────────────────
    # These are tested without persisting to the database (no restaurant_id).
    # We test scraping only and print results — no DB write.
    _banner("Known chains — spot-check only (no DB write)")

    known_chains = [
        {"name": "Dickey's Barbecue Pit", "zip": "75201"},
        {"name": "Subway", "zip": "75201"},
    ]

    for chain in known_chains:
        print(f"\n  {chain['name']} (zip: {chain['zip']})")
        try:
            from signals.sba_loans import _ensure_csv_cached, _parse_csv_file

            csv_path = _ensure_csv_cached()
            if csv_path is None:
                print("    SBA CSV not available — skipping")
                continue

            matches = _parse_csv_file(csv_path, chain["name"], chain["zip"])

            # Deduplicate by (name, date)
            seen: set[tuple] = set()
            unique: list[dict] = []
            for loan in matches:
                key = (loan.get("BorrowerName", ""), loan.get("ApprovalDate", ""))
                if key not in seen:
                    seen.add(key)
                    unique.append(loan)

            if unique:
                print(f"    Found {len(unique)} matching loan(s):")
                for loan in unique[:3]:
                    _print_loan(loan)
            else:
                print("    No matches found (none_found — not a negative signal)")

        except Exception as exc:
            print(f"    Error: {exc}")

    _banner(f"Results: {passed} passed / {failed} failed")
    if passed + failed > 0 and failed == 0:
        print("All onboarded restaurants processed without crashing.")
    print()


if __name__ == "__main__":
    main()
