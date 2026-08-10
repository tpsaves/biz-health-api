"""Test keyword_analyzer against real Outscraper data.

Run inside Docker:
    docker-compose exec scrapers python scoring/test_keyword_analyzer.py
"""

import os
import sys
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
    from scoring.keyword_analyzer import analyze_keywords, KEYWORD_FLAGS
    errors = []

    print(f"[1] KEYWORD_FLAGS categories: {list(KEYWORD_FLAGS.keys())}")
    assert len(KEYWORD_FLAGS) == 5, "Expected 5 keyword categories"

    import time
    now_ts = str(int(time.time()))

    # Test with clean reviews (no flags expected)
    clean_reviews = [
        {"review_text": "Amazing food, best BBQ in Dallas!", "review_timestamp": now_ts},
        {"review_text": "Incredible brisket, will return.", "review_timestamp": now_ts},
        {"review_text": "Long wait but worth it every time.", "review_timestamp": now_ts},
    ]
    findings = analyze_keywords(clean_reviews)
    print(f"[2] Clean reviews — flags detected: {list(findings.keys()) or 'none'}")
    if findings:
        errors.append(f"False positives on clean reviews: {list(findings.keys())}")

    # Test with reviews containing known keywords
    flagged_reviews = [
        {"review_text": "There were cockroaches near the counter.", "review_timestamp": now_ts},
        {"review_text": "Heard they have new management, food is different.", "review_timestamp": now_ts},
        {"review_text": "Quality dropped a lot, not as good as before.", "review_timestamp": now_ts},
        {"review_text": "This place has gone downhill recently.", "review_timestamp": now_ts},
        {"review_text": "Prices went up and portions smaller.", "review_timestamp": now_ts},
    ]
    findings = analyze_keywords(flagged_reviews)
    print(f"[3] Flagged reviews — categories found: {list(findings.keys())}")
    expected = {"sanitation_risk", "ownership_change", "quality_decline", "financial_stress"}
    for cat in expected:
        if cat not in findings:
            errors.append(f"Expected '{cat}' flag but not found")
        else:
            print(f"    {cat}: {findings[cat]['match_count']} match(es), examples: {findings[cat]['examples']}")

    # Test old reviews are excluded (timestamp 200 days ago)
    old_ts = str(int(time.time()) - 200 * 86400)
    old_reviews = [
        {"review_text": "There were cockroaches everywhere!", "review_timestamp": old_ts},
    ]
    findings_old = analyze_keywords(old_reviews, cutoff_days=60)
    print(f"[4] Old reviews (200d ago) excluded: {'OK' if not findings_old else 'FAILED — old review matched'}")
    if findings_old:
        errors.append("Old reviews outside cutoff window were incorrectly included")

    # Test against real Pecan Lodge Outscraper data
    engine = _build_engine()
    with Session(engine) as session:
        row = session.execute(
            text(
                """
                SELECT payload FROM raw_signals
                WHERE source = 'outscraper_reviews'
                  AND restaurant_id = (
                    SELECT id FROM restaurants WHERE name ILIKE '%Pecan Lodge%' LIMIT 1
                  )
                ORDER BY scraped_at DESC LIMIT 1
                """
            )
        ).one_or_none()

        if row is None:
            print("[5] Pecan Lodge Outscraper data not found — skipping live test")
        else:
            kf = row.payload.get("keyword_findings", {})
            dist = row.payload.get("rating_distribution", {})
            rr_recent = row.payload.get("response_rate_recent")
            rr_prior  = row.payload.get("response_rate_prior")
            print(f"[5] Pecan Lodge stored keyword_findings: {list(kf.keys()) or 'none'}")
            print(f"    rating_distribution recent_90d: {dist.get('recent_90d')}")
            print(f"    response_rate_recent={rr_recent}% prior={rr_prior}%")
            if kf:
                print("    NOTE: keyword flags present — may be legitimate (healthy = expected none)")

    if errors:
        print(f"\nFAILURES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
