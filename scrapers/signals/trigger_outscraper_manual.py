"""Manual one-time Outscraper trigger for 5 target restaurants.

Bypasses the monthly quota gate (May is exhausted from legacy 520-review runs).
Calls the Outscraper API directly at the new 46-review limit, stores results,
re-scores with engine_v2, and prints a Phase 11 signal report.

Run inside Docker:
    docker-compose exec scrapers python signals/trigger_outscraper_manual.py

Records consumed: ~5 × 46 = 230 (charged to current month quota after this run).
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, "/app")

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Exactly the 5 target restaurants (id, name, city, google_place_id)
TARGET_RESTAURANTS = [
    ("b5cca1db-6810-4b88-9a87-01f7079ecf96", "Pecan Lodge",    "Dallas",    "ChIJGXYxd92YToYR7yV_BSMQ2Xk"),
    ("885a10bd-d644-4665-b50a-fc07e697a387", "Torchy's Tacos", "Dallas",    "ChIJc5jveZEgTIYRE4tesU6NbwA"),
    ("70d11e68-859f-430b-bbdd-d9a213f50d74", "The Rustic",     "Dallas",    "ChIJhcQOLdWeToYRURVCqH8zfys"),
    ("486761c4-facb-4777-8b54-e2bae97c0c4c", "Uchi Dallas",    "Dallas",    "ChIJ7dWaEzGZToYRb3Mp_plp0AY"),
    ("d73ef90f-2f36-487f-b39a-ae5069ea028f", "Haywire",        "Dallas",    "ChIJs_xpraSZToYR53zDDkGvNs8"),
]

_MAX_REVIEWS   = 46
_OUTSCRAPER_URL = "https://api.app.outscraper.com/maps/reviews-v3"
_POLL_URL       = "https://api.app.outscraper.com/requests/{}"
_POLL_RETRIES   = 20
_POLL_INTERVAL  = 6


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _scrape_one(name: str, city: str, google_place_id: str, api_key: str) -> dict:
    """Call Outscraper directly for one restaurant — no quota gating.

    Stores the complete reviews_data array so the scoring engine can compute
    any aggregate at score time. No pre-computation at scrape time.
    """
    import httpx
    from signals.outscraper_reviews import (
        _poll_for_results,
        _extract_reviews,
        _build_monthly_breakdown,
    )

    headers = {"X-API-KEY": api_key}
    query   = f"{name} {city}".strip()

    logger.info("Scraping %s (query=%r, limit=%d)…", name, query, _MAX_REVIEWS)

    resp = httpx.get(
        _OUTSCRAPER_URL,
        params={"query": query, "limit": 1, "reviewsLimit": _MAX_REVIEWS, "sort": "newest"},
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code == 402:
        logger.warning("  402 — no credits, skipping %s", name)
        return {}
    resp.raise_for_status()
    raw = resp.json()

    if raw.get("status") in ("Pending", None) or not raw.get("data"):
        raw = _poll_for_results(raw.get("id", ""), headers)

    reviews           = _extract_reviews(raw)
    monthly_breakdown = _build_monthly_breakdown(reviews)

    return {
        "reviews_data":          reviews,
        "monthly_breakdown":     monthly_breakdown,
        "total_reviews_fetched": len(reviews),
        "google_place_id":       google_place_id,
        "name":                  name,
        "query":                 query,
    }


def _store_payload(restaurant_id: str, payload: dict, session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO raw_signals (restaurant_id, source, payload) "
            "VALUES (:rid, 'outscraper_reviews', CAST(:payload AS jsonb))"
        ),
        {"rid": restaurant_id, "payload": json.dumps(payload)},
    )
    session.execute(
        text(
            "INSERT INTO outscraper_usage (restaurant_id, records_fetched, month) "
            "VALUES (CAST(:rid AS uuid), :records, :month)"
        ),
        {
            "rid":     restaurant_id,
            "records": payload["total_reviews_fetched"],
            "month":   time.strftime("%Y-%m"),
        },
    )
    session.commit()


def _print_phase11_report(name: str, rt_before, scores: dict) -> None:
    rt_after = scores["rating_trend_score"]
    delta    = f" ({rt_after - rt_before:+d})" if isinstance(rt_before, int) else ""

    dist = {}
    if scores.get("pct_1star_recent") is not None:
        dist = {
            "pct_5star_recent": scores["pct_5star_recent"],
            "pct_1star_recent": scores["pct_1star_recent"],
        }

    rr_r = scores.get("response_rate_recent")
    rr_p = scores.get("response_rate_prior")

    kw_flags = [
        f for f, k in [
            ("sanitation_flag",               scores.get("sanitation_flag")),
            ("operational_instability_flag",  scores.get("operational_instability_flag")),
            ("ownership_change_flag",         scores.get("ownership_change_flag")),
            ("quality_decline_flag",          scores.get("quality_decline_flag")),
            ("financial_stress_flag",         scores.get("financial_stress_flag")),
        ] if k
    ]
    dist_flags = [
        f for f, k in [
            ("high_negative_rate",   scores.get("high_negative_rate")),
            ("negative_rate_rising", scores.get("negative_rate_rising")),
            ("bimodal_distribution", scores.get("bimodal_distribution")),
        ] if k
    ]
    rr_flags = [
        f for f, k in [
            ("owner_disengaged",       scores.get("owner_disengaged")),
            ("response_rate_declining",scores.get("response_rate_declining")),
        ] if k
    ]

    badges = []
    if scores.get("sanitation_flag"):        badges.append("⚠ Sanitation Risk")
    if scores.get("owner_disengaged"):       badges.append("⚠ Owner Disengaged")
    if scores.get("bimodal_distribution"):   badges.append("⚠ Polarized Reviews")
    if scores.get("ownership_change_flag"):  badges.append("⚠ Ownership Change Detected")

    print(f"\n{'─'*55}")
    print(f"  {name}")
    print(f"{'─'*55}")
    print(f"  rating_trend_score : {rt_before} → {rt_after}{delta}")
    print(f"  overall_score      : {scores['overall_score']}")

    if dist:
        print(f"  rating_dist (90d)  : ★5={dist['pct_5star_recent']:.0f}%  ★1={dist['pct_1star_recent']:.0f}%")
    else:
        print(f"  rating_dist (90d)  : insufficient data (<5 reviews in window)")

    if rr_r is not None:
        print(f"  response rate      : {rr_p}% (prior 60d) → {rr_r}% (last 60d)")
    else:
        print(f"  response rate      : insufficient data (<5 reviews per period)")

    if kw_flags:
        print(f"  keyword flags      : {', '.join(kw_flags)}")
        kf = scores.get("keyword_findings", {})
        for cat, data in kf.items():
            print(f"    {cat}: {data['match_count']} match(es) — {data['examples']}")
    else:
        print(f"  keyword flags      : none")

    all_flags = dist_flags + kw_flags + rr_flags
    if all_flags:
        print(f"  all flags          : {', '.join(all_flags)}")

    if badges:
        print(f"  demo UI badges     : {' | '.join(badges)}")
    else:
        print(f"  demo UI badges     : none")


def main() -> None:
    api_key = os.environ.get("OUTSCRAPER_API_KEY", "")
    if not api_key:
        print("ERROR: OUTSCRAPER_API_KEY not set in environment")
        sys.exit(1)

    engine = _build_engine()

    from scoring.engine_v2 import compute_scores_v2

    print(f"\nManual Outscraper trigger — {len(TARGET_RESTAURANTS)} restaurants × {_MAX_REVIEWS} reviews")
    print(f"Estimated quota consumption: ~{len(TARGET_RESTAURANTS) * _MAX_REVIEWS} records")
    print()

    results = []

    for rid, name, city, place_id in TARGET_RESTAURANTS:
        # Capture rating_trend_score before re-scoring
        with Session(engine) as session:
            prev = session.execute(
                text(
                    "SELECT rating_trend_score FROM health_scores "
                    "WHERE restaurant_id = :rid ORDER BY scored_at DESC LIMIT 1"
                ),
                {"rid": rid},
            ).one_or_none()
        rt_before = prev.rating_trend_score if prev else "—"

        # Scrape
        try:
            payload = _scrape_one(name, city, place_id, api_key)
        except Exception as exc:
            logger.error("Scrape failed for %s: %s", name, exc)
            results.append((name, rt_before, None))
            continue

        if not payload:
            results.append((name, rt_before, None))
            continue

        logger.info(
            "  %s — %d reviews fetched, %d months, kw_findings=%s",
            name, payload["total_reviews_fetched"],
            len(payload.get("monthly_breakdown", {})),
            list(payload.get("keyword_findings", {}).keys()) or "none",
        )

        # Store and re-score
        with Session(engine) as session:
            _store_payload(rid, payload, session)
            scores = compute_scores_v2(rid, session)

        results.append((name, rt_before, scores))

    # Print Phase 11 report
    print("\n" + "=" * 55)
    print("  PHASE 11 SIGNAL REPORT")
    print("=" * 55)

    for name, rt_before, scores in results:
        if scores is None:
            print(f"\n  {name}: SCRAPE FAILED")
        else:
            _print_phase11_report(name, rt_before, scores)

    # Quota summary
    with Session(engine) as session:
        month = time.strftime("%Y-%m")
        used = session.execute(
            text("SELECT COALESCE(SUM(records_fetched),0) FROM outscraper_usage WHERE month = :m"),
            {"m": month},
        ).scalar()
    print(f"\n{'─'*55}")
    print(f"  Quota after run: {used:,} / 10,000 records used ({month})")
    print()


if __name__ == "__main__":
    main()
