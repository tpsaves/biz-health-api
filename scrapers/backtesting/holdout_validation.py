"""Phase 13 Part 1 — Train/Test Holdout Validation & Leakage Audit.

Out-of-time split:
  Cutoff: 2022-01-01
  Train: closed restaurants with closure_date before 2022 (2017-2021)
  Test:  closed restaurants with closure_date on/after 2022 (2022-2023)

Rule: scoring thresholds may only be derived from the train set.
This script reports honest test-set performance and audits each rule for leakage.
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

_OUTPUT_DIR  = Path("/backtesting_results")
_TRAIN_CUTOFF = date(2022, 1, 1)

# Every explicit scoring rule in engine_v2.py, with derivation and leakage verdict.
# test_set_used=True means the rule was tuned using observations from the test set.
_RULES = [
    {
        "rule":         "Days since last review > 45 → -20 velocity score",
        "derived_from": "Domain knowledge — review gap indicates disengagement",
        "test_set_used": False,
    },
    {
        "rule":         "Monthly volume sharply_declining → -25 velocity score",
        "derived_from": "Domain knowledge — declining engagement signal",
        "test_set_used": False,
    },
    {
        "rule":         "1-star spike > 30% of recent reviews → -15 velocity score",
        "derived_from": "Domain knowledge — reputation signal",
        "test_set_used": False,
    },
    {
        "rule":         "Rating slope <= -0.5 → -25 rating score (sharp_decline)",
        "derived_from": "Domain knowledge — deteriorating customer sentiment",
        "test_set_used": False,
    },
    {
        "rule":         "SBA default (charge-off) → cap overall_score at 45",
        "derived_from": "Phase 8 financial risk analysis — domain knowledge, not restaurant-specific",
        "test_set_used": False,
    },
    {
        "rule":         "Tax delinquent 2+ years → cap overall_score at 55",
        "derived_from": "Phase 8 financial risk analysis — domain knowledge, not restaurant-specific",
        "test_set_used": False,
    },
    {
        "rule":         "Composite cap: operational_score < 65 AND review_velocity_score < 30 → cap at 59",
        "derived_from": (
            "Phase 10 false-negative analysis — derived by observing which restaurants scored "
            "moderate but then closed. The 3 test-set restaurants (STEEL, MI PUEBLITO, THE LAST STAND) "
            "were exactly the false negatives that prompted this rule. "
            "Recall improved from 77.8% to 100% BECAUSE this rule was fitted to the full dataset "
            "including the test set."
        ),
        "test_set_used": True,  # LEAKAGE — see above
    },
    {
        "rule":         "businessStatus == TEMPORARILY_CLOSED -> -30 operational",
        "derived_from": "Phase 14 — domain knowledge. Google Maps TEMPORARILY_CLOSED is "
                        "definitionally a closure precursor. Cannot be retroactively measured "
                        "for the retrospective cohort; will appear in prospective outcomes (Sep/Dec 2026).",
        "test_set_used": False,
    },
    {
        "rule":         "businessStatus == PERMANENTLY_CLOSED -> cap overall_score at 20",
        "derived_from": "Phase 14 — domain knowledge. Google Maps PERMANENTLY_CLOSED confirmation.",
        "test_set_used": False,
    },
    {
        "rule":         "hours_reduction_pct >= 30 -> -15 operational",
        "derived_from": "Phase 14 — domain knowledge threshold. Requires two hours_monitor snapshots; "
                        "not reconstructable for retrospective cohort. Will appear in prospective outcomes.",
        "test_set_used": False,
    },
]


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB",   "bizhealth")
    user = os.environ.get("POSTGRES_USER",  "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _load_t90_records(session: Session) -> list[dict]:
    """Load one row per restaurant — the T-90 reconstruction."""
    rows = session.execute(
        text(
            """
            SELECT
                r.name,
                bc.baseline_score,
                bc.baseline_risk_band,
                bc.closure_date,
                bc.baseline_factors
            FROM backtest_cohort bc
            JOIN restaurants r ON bc.restaurant_id = r.id
            WHERE bc.cohort_type = 'retrospective'
              AND bc.notes LIKE '%T-90%'
            ORDER BY bc.closure_date
            """
        )
    ).fetchall()

    records = []
    for row in rows:
        factors = {}
        if row.baseline_factors:
            raw = row.baseline_factors
            factors = json.loads(raw) if isinstance(raw, str) else raw

        records.append({
            "name":                  row.name,
            "baseline_score":        row.baseline_score or 0,
            "risk_band":             row.baseline_risk_band or "unknown",
            "closure_date":          str(row.closure_date),
            "composite_risk_cap":    bool(factors.get("composite_risk_cap", False)),
            "operational_score":     factors.get("operational_score", 0),
            "review_velocity_score": factors.get("review_velocity_score", 0),
        })

    return records


def _compute_metrics(records: list[dict], use_composite_cap: bool) -> dict:
    """
    Precision, recall, F1 at the elevated/high threshold (score <= 59).
    All records are confirmed closures, so actually_closed=True for all.
    """
    tp = fp = fn = 0
    for r in records:
        # When composite cap is excluded, a restaurant whose cap fired would
        # have scored > 59 (we know this because the cap only triggers when the
        # natural score is above the cap ceiling).
        if not use_composite_cap and r["composite_risk_cap"]:
            predicted_at_risk = False  # would have been moderate without the cap
        else:
            predicted_at_risk = r["baseline_score"] <= 59

        if predicted_at_risk:
            tp += 1  # correctly flagged (actually closed)
        else:
            fn += 1  # missed (closed but not flagged)

    precision = 100.0 if tp > 0 else None  # no false positives in retrospective set
    recall    = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 1)

    return {
        "true_positives":  tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision_pct":   precision,
        "recall_pct":      recall,
        "f1_pct":          f1,
    }


def run_holdout_validation(session: Session) -> dict:
    records = _load_t90_records(session)

    if not records:
        print("No retrospective T-90 records found — run historical_reconstructor.py first.")
        return {}

    train = [r for r in records if r["closure_date"] < str(_TRAIN_CUTOFF)]
    test  = [r for r in records if r["closure_date"] >= str(_TRAIN_CUTOFF)]

    print("\n" + "=" * 72)
    print("HOLDOUT VALIDATION — OUT-OF-TIME TRAIN/TEST SPLIT")
    print("=" * 72)
    print(f"\nSplit cutoff: {_TRAIN_CUTOFF}")
    print(f"Train set: closure_date < {_TRAIN_CUTOFF}  ({len(train)} restaurants)")
    print(f"Test set:  closure_date >= {_TRAIN_CUTOFF} ({len(test)} restaurants)")

    print("\nTRAIN SET:")
    for r in train:
        cap = "  [cap not triggered]" if not r["composite_risk_cap"] else "  [composite cap]"
        print(f"  {r['name'][:38]:<38} closure={r['closure_date']}  score={r['baseline_score']:>3} ({r['risk_band']}){cap}")

    print("\nTEST SET:")
    for r in test:
        cap = "  [composite cap — LEAKAGE]" if r["composite_risk_cap"] else "  [cap not triggered]"
        print(f"  {r['name'][:38]:<38} closure={r['closure_date']}  score={r['baseline_score']:>3} ({r['risk_band']}){cap}")

    train_m  = _compute_metrics(train, use_composite_cap=True)
    test_m   = _compute_metrics(test,  use_composite_cap=True)
    honest_m = _compute_metrics(test,  use_composite_cap=False)

    print("\n" + "-" * 72)
    print("TRAIN SET PERFORMANCE (all rules including composite cap)")
    print(f"  Precision: {train_m['precision_pct']}%  Recall: {train_m['recall_pct']}%  F1: {train_m['f1_pct']}%")
    print(f"  TP={train_m['true_positives']} FP={train_m['false_positives']} FN={train_m['false_negatives']}")

    print("\nTEST SET PERFORMANCE — with composite cap  ⚠ LEAKAGE PRESENT")
    print(f"  Precision: {test_m['precision_pct']}%  Recall: {test_m['recall_pct']}%  F1: {test_m['f1_pct']}%")
    print(f"  TP={test_m['true_positives']} FP={test_m['false_positives']} FN={test_m['false_negatives']}")
    print("  Caution: composite cap was tuned by observing these very restaurants.")

    print("\nTEST SET PERFORMANCE — without composite cap  ✓ HONEST out-of-time result")
    print(f"  Precision: {honest_m['precision_pct']}%  Recall: {honest_m['recall_pct']}%  F1: {honest_m['f1_pct']}%")
    print(f"  TP={honest_m['true_positives']} FP={honest_m['false_positives']} FN={honest_m['false_negatives']}")
    print("  → Uses only rules that could have been derived from the train set alone.")

    print("\n" + "=" * 72)
    print("LEAKAGE AUDIT — ALL SCORING RULES IN engine_v2.py")
    print("=" * 72)

    any_fail = False
    for rule in _RULES:
        verdict = "FAIL" if rule["test_set_used"] else "PASS"
        if rule["test_set_used"]:
            any_fail = True
        print(f"\n  [{verdict}] {rule['rule']}")
        print(f"    Source: {rule['derived_from']}")

    print("\n" + "-" * 72)
    if any_fail:
        print("OVERALL AUDIT: FAIL")
        print("  One rule (composite risk cap) was derived from data that includes the test set.")
        print("  Honest fix: retune the composite cap thresholds using only train-set closures,")
        print("  OR disclose the leakage and present the cap-free test recall as the primary number.")
    else:
        print("OVERALL AUDIT: PASS")
    print("=" * 72 + "\n")

    print("CONFIRMATION:")
    print("  No test-set restaurant was used to derive any non-composite-cap threshold.")
    print("  All other thresholds are domain knowledge or derived from 2017-2021 closures only.")
    print(f"  Test set restaurants: {', '.join(r['name'] for r in test)}\n")

    print()
    print("PHASE 14 SIGNALS (forward-looking — not in retrospective cohort):")
    print("  businessStatus (TEMPORARILY_CLOSED): domain knowledge, PASS")
    print("  hours_reduction >= 30%: domain knowledge, PASS")
    print("  Both require live scraper data — retroactive measurement not possible.")
    print("  Impact on train/test recall: none (data gap, not a rule gap)")
    print()

    result = {
        "generated_at":  date.today().isoformat(),
        "train_cutoff":  str(_TRAIN_CUTOFF),
        "train_set": {
            "count":       len(train),
            "date_range":  f"{train[0]['closure_date']} to {train[-1]['closure_date']}" if train else "",
            "restaurants": [r["name"] for r in train],
        },
        "test_set": {
            "count":       len(test),
            "date_range":  f"{test[0]['closure_date']} to {test[-1]['closure_date']}" if test else "",
            "restaurants": [r["name"] for r in test],
        },
        "train_performance": {**train_m, "note": "All rules applied including composite cap."},
        "test_performance_with_cap": {
            **test_m,
            "caveat": "Composite cap was derived from these test-set restaurants — result is optimistic.",
        },
        "test_performance_honest": {
            **honest_m,
            "note": "Uses only rules that could have been derived from the train set alone.",
        },
        "leakage_audit": [
            {
                "rule":         r["rule"],
                "derived_from": r["derived_from"],
                "leakage":      r["test_set_used"],
                "verdict":      "FAIL" if r["test_set_used"] else "PASS",
            }
            for r in _RULES
        ],
        "overall_leakage_verdict": "FAIL" if any_fail else "PASS",
    }

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / "holdout_validation.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Results saved to {out_path}")
    return result


if __name__ == "__main__":
    engine = _build_engine()
    with Session(engine) as session:
        run_holdout_validation(session)
