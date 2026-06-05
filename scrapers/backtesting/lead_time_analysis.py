"""Phase 13 Part 2 — Lead-Time Early Warning Exhibit.

For each closed restaurant in the retrospective cohort, uses the T-90 and T-180
reconstructed scores to measure how many days before closure the score first
crossed each risk threshold (< 60 moderate, < 40 high).

We have two data points per restaurant (T-90 and T-180). Lead times are
conservative lower bounds: if T-180 score is below threshold, we report ≥180 days.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("/backtesting_results")

_THRESHOLDS = [
    {"label": "Score below 60 (moderate risk)", "threshold": 60, "key": "below_60"},
    {"label": "Score below 40 (high risk)",     "threshold": 40, "key": "below_40"},
]


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB",   "bizhealth")
    user = os.environ.get("POSTGRES_USER",  "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _load_scores_by_restaurant(session: Session) -> list[dict]:
    """Load T-90 and T-180 scores per unique restaurant."""
    rows = session.execute(
        text(
            """
            SELECT
                r.name,
                bc.baseline_score,
                bc.closure_date,
                bc.notes,
                bc.baseline_factors
            FROM backtest_cohort bc
            JOIN restaurants r ON bc.restaurant_id = r.id
            WHERE bc.cohort_type = 'retrospective'
            ORDER BY bc.closure_date, r.name, bc.baseline_date
            """
        )
    ).fetchall()

    # Group by restaurant name.
    by_name: dict[str, dict] = {}
    for row in rows:
        name   = row.name
        label  = "T-90" if "T-90" in (row.notes or "") else "T-180"
        factors = {}
        if row.baseline_factors:
            raw = row.baseline_factors
            factors = json.loads(raw) if isinstance(raw, str) else raw

        if name not in by_name:
            by_name[name] = {"closure_date": str(row.closure_date)}

        by_name[name][label] = {
            "score":              row.baseline_score or 0,
            "composite_risk_cap": bool(factors.get("composite_risk_cap", False)),
            "volume_trend":       factors.get("volume_trend", "unknown"),
            "review_count":       factors.get("review_count", 0),
        }

    # Return as sorted list.
    return [{"name": name, **data} for name, data in sorted(by_name.items(), key=lambda x: x[1].get("closure_date", ""))]


def _earliest_lead_time(restaurant: dict, threshold: int) -> int | None:
    """
    Return the earliest detected lead time (days before closure) that score < threshold.
    Conservative lower bound: we only know data at T-90 and T-180.
    """
    t180_score = restaurant.get("T-180", {}).get("score", 100)
    t90_score  = restaurant.get("T-90",  {}).get("score", 100)

    if t180_score < threshold:
        return 180  # flagged at 180 days before closure (possibly earlier — unknown)
    if t90_score < threshold:
        return 90   # flagged between T-90 and T-180 — record conservatively as 90
    return None     # never crossed this threshold in available data


def run_lead_time_analysis(session: Session) -> dict:
    restaurants = _load_scores_by_restaurant(session)

    if not restaurants:
        print("No retrospective records found — run historical_reconstructor.py first.")
        return {}

    print("\n" + "=" * 72)
    print("LEAD-TIME EARLY WARNING EXHIBIT")
    print("=" * 72)
    print(f"\nRestaurants analyzed:    {len(restaurants)}")
    print("Data points available:   T-90 and T-180 before closure")
    print("Lead times are lower bounds — actual first-crossing may be earlier.\n")

    # Per-restaurant lead times.
    details = []
    for r in restaurants:
        row = {"name": r["name"], "closure_date": r["closure_date"]}
        row["t90_score"]  = r.get("T-90",  {}).get("score", "N/A")
        row["t180_score"] = r.get("T-180", {}).get("score", "N/A")
        for cfg in _THRESHOLDS:
            row[cfg["key"]] = _earliest_lead_time(r, cfg["threshold"])
        details.append(row)

    # Aggregate per threshold.
    summary = []
    total = len(details)
    for cfg in _THRESHOLDS:
        key = cfg["key"]
        lead_times = [d[key] for d in details if d[key] is not None]
        flagged = len(lead_times)

        if lead_times:
            avg_lead   = sum(lead_times) / len(lead_times)
            pct_30d    = round(sum(1 for lt in lead_times if lt >= 30)  / total * 100, 1)
            pct_60d    = round(sum(1 for lt in lead_times if lt >= 60)  / total * 100, 1)
            pct_90d    = round(sum(1 for lt in lead_times if lt >= 90)  / total * 100, 1)
        else:
            avg_lead   = None
            pct_30d    = pct_60d = pct_90d = 0.0

        summary.append({
            "threshold_label":       cfg["label"],
            "key":                   key,
            "threshold_score":       cfg["threshold"],
            "restaurants_flagged":   flagged,
            "total_restaurants":     total,
            "avg_lead_days":         round(avg_lead) if avg_lead else None,
            "pct_flagged_30d_plus":  pct_30d,
            "pct_flagged_60d_plus":  pct_60d,
            "pct_flagged_90d_plus":  pct_90d,
        })

    # Print summary table.
    hdr = f"{'Threshold crossed':<32} {'Avg lead time':>14} {'% flagged 30d+':>15} {'% flagged 90d+':>15}"
    print(hdr)
    print("-" * len(hdr))
    for ts in summary:
        avg   = f"{ts['avg_lead_days']}d" if ts["avg_lead_days"] else "N/A"
        p30   = f"{ts['pct_flagged_30d_plus']}%"
        p90   = f"{ts['pct_flagged_90d_plus']}%"
        print(f"{ts['threshold_label']:<32} {avg:>14} {p30:>15} {p90:>15}")

    print()
    below60 = next(ts for ts in summary if ts["key"] == "below_60")
    if below60["avg_lead_days"]:
        print(
            f"On average our score flagged distressed restaurants "
            f"{below60['avg_lead_days']} days before closure\n"
            f"({below60['pct_flagged_90d_plus']}% of closures were flagged at least 90 days in advance)\n"
        )

    # Individual examples (up to 5).
    print("Individual examples (first 5 restaurants):")
    print("-" * 72)
    for d in details[:5]:
        lt60 = d.get("below_60")
        lt40 = d.get("below_40")
        lt60_str = f"≥{lt60}d before closure" if lt60 else "never dropped below 60"
        lt40_str = f"≥{lt40}d before closure" if lt40 else "never dropped below 40"
        print(f"  {d['name'][:42]:<42} closure={d['closure_date']}")
        print(f"    T-90 score={d['t90_score']}   T-180 score={d['t180_score']}")
        print(f"    First below 60: {lt60_str}")
        print(f"    First below 40: {lt40_str}")
        print()

    plain_english = (
        f"On average our score flagged distressed restaurants "
        f"{below60['avg_lead_days']} days before closure. "
        f"{below60['pct_flagged_90d_plus']}% of closures were flagged at least 90 days in advance."
    ) if below60["avg_lead_days"] else "Insufficient data for lead-time calculation."

    result = {
        "generated_at":                date.today().isoformat(),
        "total_analyzed":              total,
        "data_points_per_restaurant":  "T-90 and T-180 (lower bounds on lead time)",
        "threshold_summary":           summary,
        "restaurant_details":          details,
        "plain_english":               plain_english,
    }

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / "lead_time.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Results saved to {out_path}")
    return result


if __name__ == "__main__":
    engine = _build_engine()
    with Session(engine) as session:
        run_lead_time_analysis(session)
