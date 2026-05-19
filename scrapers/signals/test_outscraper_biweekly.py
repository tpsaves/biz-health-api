"""Smoke test for biweekly Outscraper configuration.

Confirms reviewsLimit is 46, scheduler has biweekly CronTrigger on days 1 and 15,
runs a single restaurant scrape with the new limit, and verifies quota logging.

Run inside Docker:
    docker-compose exec scrapers python signals/test_outscraper_biweekly.py
"""

import os
import sys

# Ensure /app is on the path so that outscraper_reviews.py can resolve
# its own "from signals.outscraper_quota import ..." when imported here.
sys.path.insert(0, "/app")

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
    errors = []

    # 1 — confirm _MAX_REVIEWS is 46
    from signals.outscraper_reviews import _MAX_REVIEWS
    print(f"[1] _MAX_REVIEWS = {_MAX_REVIEWS}")
    if _MAX_REVIEWS != 46:
        errors.append(f"_MAX_REVIEWS expected 46 but got {_MAX_REVIEWS}")

    # 2 — confirm scheduler CronTrigger uses day='1,15'
    import inspect
    import main as scheduler_main
    src = inspect.getsource(scheduler_main.main)
    if "day=\"1,15\"" in src or "day='1,15'" in src:
        print("[2] Scheduler: biweekly CronTrigger on days 1 and 15 — OK")
    else:
        errors.append("Scheduler does not contain CronTrigger(day='1,15') — check main.py")
        print("[2] Scheduler: biweekly CronTrigger NOT found in source")

    engine = _build_engine()
    with Session(engine) as session:
        # 3 — monthly summary shows projected_monthly and schedule fields
        from signals.outscraper_quota import monthly_summary
        summary = monthly_summary(session)
        projected = summary.get("projected_monthly")
        schedule  = summary.get("schedule")
        print(f"[3] monthly_summary projected_monthly={projected} schedule='{schedule}'")
        if projected != 9844:
            errors.append(f"projected_monthly expected 9844 but got {projected}")
        if schedule != "biweekly (1st and 15th)":
            errors.append(f"schedule expected 'biweekly (1st and 15th)' but got '{schedule}'")

        # 4 — single restaurant scrape (only runs if API key is set and quota allows)
        api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
        if not api_key:
            print("[4] OUTSCRAPER_API_KEY not set — skipping live scrape test")
        else:
            row = session.execute(
                text(
                    "SELECT id, name, city, google_place_id FROM restaurants "
                    "WHERE google_place_id IS NOT NULL LIMIT 1"
                )
            ).fetchone()
            if row is None:
                print("[4] No restaurants found — skipping live scrape test")
            else:
                from signals.outscraper_quota import check_quota
                quota_before = check_quota(_MAX_REVIEWS, session)
                if not quota_before["allowed"]:
                    print(f"[4] Quota exhausted ({quota_before['remaining']} remaining) — skipping live scrape test")
                else:
                    from signals.outscraper_reviews import scrape_outscraper_reviews
                    print(f"[4] Scraping '{row.name}' with reviewsLimit={_MAX_REVIEWS}…")
                    result = scrape_outscraper_reviews(
                        row.google_place_id, row.name, str(row.id), session, city=row.city or ""
                    )
                    fetched = result.get("total_reviews_fetched", 0)
                    print(f"[4] Records fetched: {fetched} (limit was {_MAX_REVIEWS})")
                    if fetched > _MAX_REVIEWS:
                        errors.append(f"Fetched {fetched} reviews but limit is {_MAX_REVIEWS}")

                    quota_after = check_quota(_MAX_REVIEWS, session)
                    print(f"[4] Quota after scrape: {quota_after['used']} used, {quota_after['remaining']} remaining")

    if errors:
        print("\nFAILURES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
