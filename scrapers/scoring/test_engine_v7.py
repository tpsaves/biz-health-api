"""Phase 11 integration test: rating distribution, keyword flags, response rate trend.

Re-scores all active pipeline restaurants and prints rating_trend_score before/after
with any Phase 11 flags triggered.

Run inside Docker:
    docker-compose exec scrapers python scoring/test_engine_v7.py
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


def main() -> None:
    engine = create_engine(_db_url())

    with Session(engine) as session:
        rows = session.execute(
            text(
                "SELECT id, name FROM restaurants "
                "WHERE google_place_id IS NOT NULL ORDER BY name"
            )
        ).fetchall()

    if not rows:
        print("No restaurants found.")
        sys.exit(1)

    print(f"\nRe-scoring {len(rows)} restaurants for Phase 11 signals...\n")
    print(f"  {'Name':<38} {'RT Before':>9} {'RT After':>9}  Flags")
    print("  " + "-" * 82)

    any_flagged     = False
    pecan_result    = None
    backyard_result = None

    for restaurant_id, name in rows:
        rid = str(restaurant_id)

        with Session(engine) as session:
            prev = session.execute(
                text(
                    "SELECT rating_trend_score FROM health_scores "
                    "WHERE restaurant_id = :rid ORDER BY scored_at DESC LIMIT 1"
                ),
                {"rid": rid},
            ).one_or_none()
            rt_before = prev.rating_trend_score if prev else "—"

            try:
                scores = compute_scores_v2(rid, session)
            except Exception as exc:
                print(f"  {name:<38}  ERROR: {exc}")
                continue

        rt_after = scores["rating_trend_score"]
        flags = []
        if scores.get("high_negative_rate"):            flags.append("high_neg_rate")
        if scores.get("negative_rate_rising"):          flags.append("neg_rate_rising")
        if scores.get("bimodal_distribution"):          flags.append("bimodal")
        if scores.get("sanitation_flag"):               flags.append("SANITATION")
        if scores.get("operational_instability_flag"):  flags.append("op_instability")
        if scores.get("ownership_change_flag"):         flags.append("ownership_change")
        if scores.get("quality_decline_flag"):          flags.append("quality_decline")
        if scores.get("financial_stress_flag"):         flags.append("financial_stress")
        if scores.get("owner_disengaged"):              flags.append("OWNER_DISENGAGED")
        if scores.get("response_rate_declining"):       flags.append("resp_declining")
        if scores.get("tabc_expected_missing"):         flags.append("TABC_MISSING")
        if scores.get("inspection_expected_missing"):   flags.append("INSP_MISSING")

        if flags:
            any_flagged = True

        print(f"  {name:<38} {str(rt_before):>9} {str(rt_after):>9}  {', '.join(flags)}")

        if "pecan lodge" in name.lower():
            pecan_result = (rt_before, rt_after, flags, scores)
        if "backyard" in name.lower():
            backyard_result = (name, flags, scores)

    print()

    # Summary checks
    if pecan_result:
        rt_b, rt_a, pl_flags, pl_scores = pecan_result
        dangerous = {"SANITATION", "OWNER_DISENGAGED"}
        print(f"[CHECK] Pecan Lodge rating_trend: {rt_b} → {rt_a}, flags={pl_flags or 'none'}")
        pl_dangerous = [f for f in pl_flags if f in dangerous]
        if pl_dangerous:
            print(f"  NOTE: {pl_dangerous} flagged — review manually for false positive")
        else:
            print("  Pecan Lodge: no high-severity flags (expected for healthy restaurant)")
    else:
        print("[CHECK] Pecan Lodge not in active restaurant list")

    if any_flagged:
        print("[CHECK] At least one restaurant triggered a Phase 11 flag — OK")
    else:
        print("[CHECK] No Phase 11 flags triggered — may be expected if Outscraper data is sparse")

    # Signal confidence checks
    if backyard_result:
        b_name, b_flags, b_scores = backyard_result
        tabc_conf = b_scores.get("tabc_confidence")
        tabc_miss = b_scores.get("tabc_expected_missing")
        print(f"[CHECK] Backyard Dallas tabc_confidence={tabc_conf!r} tabc_expected_missing={tabc_miss}")
        if tabc_miss:
            print("  PASS: Backyard Dallas correctly flagged tabc_expected_missing (bar with no TABC)")
        else:
            print("  NOTE: tabc_expected_missing=False — place_types may not include 'bar' in stored signal")
    else:
        print("[CHECK] Backyard Dallas not in restaurant list")

    # Verify new columns are written to DB
    with Session(engine) as session:
        recent_count = session.execute(
            text(
                "SELECT COUNT(*) FROM health_scores "
                "WHERE scored_at > NOW() - INTERVAL '10 minutes'"
            )
        ).scalar()
        with_dist = session.execute(
            text(
                "SELECT COUNT(*) FROM health_scores "
                "WHERE scored_at > NOW() - INTERVAL '10 minutes' "
                "  AND pct_1star_recent IS NOT NULL"
            )
        ).scalar()
        with_keywords = session.execute(
            text(
                "SELECT COUNT(*) FROM health_scores "
                "WHERE scored_at > NOW() - INTERVAL '10 minutes' "
                "  AND keyword_findings IS NOT NULL"
            )
        ).scalar()

    print(f"[CHECK] Rows written this run: {recent_count}")
    print(f"[CHECK] Rows with rating distribution: {with_dist}")
    print(f"[CHECK] Rows with keyword_findings: {with_keywords}")
    print()


if __name__ == "__main__":
    main()
