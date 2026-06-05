"""Phase 13 Part 3 — Score-Band to Closure-Rate Table.

Groups retrospective closed restaurants by their T-90 score band and computes
the closure rate per band. Outcome is labeled as 'closure rate (proxy outcome)'
since restaurant closure is a proxy for payment default — the real distributor
outcome to be validated with partner receivables data.

Prospective cohort outcomes are pending (due September 2026) and are excluded
from this table. When outcomes resolve, this script should be re-run.
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

_BANDS = [
    {"label": "80-100", "low": 80, "high": 100, "name": "Low",      "key": "low"},
    {"label": "60-79",  "low": 60, "high": 79,  "name": "Moderate", "key": "moderate"},
    {"label": "40-59",  "low": 40, "high": 59,  "name": "Elevated", "key": "elevated"},
    {"label": "0-39",   "low":  0, "high": 39,  "name": "High",     "key": "high"},
]


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB",   "bizhealth")
    user = os.environ.get("POSTGRES_USER",  "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _load_t90_records(session: Session) -> list[dict]:
    """T-90 score per closed restaurant (retrospective cohort only)."""
    rows = session.execute(
        text(
            """
            SELECT r.name, bc.baseline_score, bc.closure_date
            FROM backtest_cohort bc
            JOIN restaurants r ON bc.restaurant_id = r.id
            WHERE bc.cohort_type = 'retrospective'
              AND bc.notes LIKE '%T-90%'
            ORDER BY bc.closure_date
            """
        )
    ).fetchall()

    return [
        {
            "name":           row.name,
            "score":          row.baseline_score or 0,
            "closure_date":   str(row.closure_date),
            "closed":         True,  # all retrospective records are confirmed closures
        }
        for row in rows
    ]


def _assign_band(score: int) -> str:
    if score >= 80: return "low"
    if score >= 60: return "moderate"
    if score >= 40: return "elevated"
    return "high"


def run_band_outcome_table(session: Session) -> dict:
    records = _load_t90_records(session)

    if not records:
        print("No retrospective T-90 records — run historical_reconstructor.py first.")
        return {}

    # Group by band.
    band_groups: dict[str, list] = {b["key"]: [] for b in _BANDS}
    for r in records:
        band = _assign_band(r["score"])
        band_groups[band].append(r)

    total_closed = sum(1 for r in records if r["closed"])
    cumulative_closed = 0

    print("\n" + "=" * 72)
    print("SCORE-BAND OUTCOME TABLE")
    print("Outcome: closure rate (proxy outcome)")
    print("Note: closure is a proxy for payment default — the real distributor")
    print("outcome will be validated with partner receivables data.")
    print("=" * 72)
    print()

    rows_out = []
    prev_rate = -1.0
    monotonic = True

    hdr = f"{'Score Band':<12} {'Count':>7} {'Closure Rate':>14} {'Cum % Closures':>16}"
    print(hdr)
    print("-" * len(hdr))

    for band_cfg in _BANDS:
        key     = band_cfg["key"]
        label   = f"{band_cfg['name']} ({band_cfg['label']})"
        group   = band_groups[key]
        n       = len(group)
        closed  = sum(1 for r in group if r["closed"])

        closure_rate = round(closed / n * 100, 1) if n > 0 else None
        cumulative_closed += closed
        cum_pct = round(cumulative_closed / total_closed * 100, 1) if total_closed > 0 else 0.0

        # Check monotonicity (closure rate should increase as score worsens).
        if closure_rate is not None and prev_rate >= 0 and closure_rate < prev_rate:
            monotonic = False
        if closure_rate is not None:
            prev_rate = closure_rate

        rate_str = f"{closure_rate}%" if closure_rate is not None else "N/A (no data)"
        cum_str  = f"{cum_pct}%"
        print(f"{label:<12} {n:>7} {rate_str:>14} {cum_str:>16}")

        rows_out.append({
            "score_band":       band_cfg["label"],
            "band_name":        band_cfg["name"],
            "count":            n,
            "closed":           closed,
            "closure_rate_pct": closure_rate,
            "cum_pct_closures": cum_pct,
        })

    print()
    print(f"Total retrospective records: {len(records)} (all confirmed closures)")
    print(f"Outcome type: closure rate (proxy outcome — see methodology_notes.md)")
    print(f"Monotonic across bands: {'YES' if monotonic else 'NO — review band assignments'}")

    if not any(band_groups["low"] + band_groups["moderate"]):
        print("\nNote: No retrospective data in low or moderate bands.")
        print("      These bands will populate when prospective outcomes resolve (Sep/Dec 2026).")

    result = {
        "generated_at":     date.today().isoformat(),
        "outcome_label":    "closure rate (proxy outcome)",
        "proxy_caveat":     "Restaurant closure is a proxy for payment default. Validate with partner receivables data.",
        "data_source":      "Retrospective cohort only — prospective outcomes pending September 2026.",
        "total_records":    len(records),
        "is_monotonic":     monotonic,
        "bands":            rows_out,
    }

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / "band_outcome_table.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to {out_path}")
    return result


if __name__ == "__main__":
    engine = _build_engine()
    with Session(engine) as session:
        run_band_outcome_table(session)
