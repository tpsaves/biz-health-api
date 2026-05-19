"""Smoke test for the Outscraper monthly quota tracker.

Run inside Docker:
    docker-compose exec scrapers python signals/test_outscraper_quota.py
"""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

load_dotenv()


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def main():
    engine = _build_engine()
    errors = []

    with Session(engine) as session:
        # 1 — verify the table exists and has rows
        row_count = session.execute(
            text("SELECT COUNT(*) FROM outscraper_usage")
        ).scalar()
        print(f"[1] outscraper_usage rows: {row_count}")
        if row_count == 0:
            errors.append("outscraper_usage is empty — backfill may not have run")

        # 2 — monthly summary
        from outscraper_quota import monthly_summary, check_quota, MONTHLY_CAP
        summary = monthly_summary(session)
        print(
            f"[2] Monthly summary: {summary['records_used']:,} / {summary['cap']:,} records "
            f"({summary['estimated_cost']}, {summary['pct_used']}% used)"
        )

        # 3 — quota check for a single restaurant (30 records)
        quota = check_quota(30, session)
        status = "ALLOWED" if quota["allowed"] else "BLOCKED"
        print(
            f"[3] check_quota(30): {status} — {quota['remaining']:,} remaining "
            f"({quota['used']:,} used this month)"
        )

        # 4 — verify cap value
        print(f"[4] MONTHLY_CAP = {MONTHLY_CAP:,}")
        if MONTHLY_CAP != 10_000:
            errors.append(f"MONTHLY_CAP expected 10,000 but got {MONTHLY_CAP}")

        # 5 — verify cost calculation
        expected_cost = summary["records_used"] / 1000 * 3.00
        print(f"[5] Cost cross-check: ${expected_cost:.2f} (summary says {summary['estimated_cost']})")

    if errors:
        print("\nFAILURES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
