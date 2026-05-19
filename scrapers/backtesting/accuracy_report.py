"""Backtesting Part 5 — Accuracy Report.

Reads all retrospective cohort records with known closure outcomes and computes:
  - Closure rate by risk band (90d and 180d)
  - Overall precision and recall at the high/elevated risk threshold
  - AUC-ROC score for model discrimination
  - Signal importance ranking (most degraded signals in closed vs open restaurants)
  - Plain English summary suitable for a customer pitch

Saves report JSON to /backtesting_results/report_{date}.json.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("/backtesting_results")

# Risk bands in ascending risk order.
_BANDS = ["low", "moderate", "elevated", "high"]

# Threshold for "at-risk" classification (elevated + high).
_AT_RISK_THRESHOLD = 59  # score <= 59 → at-risk


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_accuracy_report(session: Session) -> dict:
    """Compute all accuracy metrics and return a structured report dict."""
    records = _load_retrospective_records(session)

    if not records:
        logger.warning("[accuracy] No retrospective records available — returning empty report")
        return _empty_report()

    band_stats   = _compute_band_stats(records)
    overall      = _compute_overall_metrics(records)
    signal_stats = _compute_signal_importance(records)
    summary_text = _build_plain_english_summary(records, band_stats, overall, signal_stats)

    report = {
        "generated_at":     datetime.utcnow().isoformat() + "Z",
        "total_backtested": len(records),
        "band_breakdown":   band_stats,
        "overall":          overall,
        "signal_importance": signal_stats,
        "plain_english_summary": summary_text,
    }

    _save_report(report)
    logger.info("[accuracy] Report generated for %d restaurants", len(records))
    return report


def _empty_report() -> dict:
    return {
        "generated_at":     datetime.utcnow().isoformat() + "Z",
        "total_backtested": 0,
        "band_breakdown":   {},
        "overall":          {"precision": None, "recall": None, "auc_roc": None},
        "signal_importance": [],
        "plain_english_summary": (
            "No retrospective backtest data available yet. "
            "Run closed_restaurant_finder.py and historical_reconstructor.py first."
        ),
    }


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_retrospective_records(session: Session) -> list[dict]:
    """Load retrospective cohort rows that have enough data to evaluate."""
    rows = session.execute(
        text(
            """
            SELECT
                bc.id,
                bc.restaurant_id,
                bc.baseline_score,
                bc.baseline_risk_band,
                bc.baseline_factors,
                bc.closure_date,
                bc.outcome_90d,
                bc.outcome_180d
            FROM backtest_cohort bc
            WHERE bc.cohort_type = 'retrospective'
            ORDER BY bc.baseline_date
            """
        )
    ).fetchall()

    records = []
    for row in rows:
        factors = {}
        if row.baseline_factors:
            try:
                raw = row.baseline_factors
                factors = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass

        closed_90d  = _is_closed_outcome(row.outcome_90d)
        closed_180d = _is_closed_outcome(row.outcome_180d)

        # Use closure_date as ground truth when outcome fields aren't populated yet.
        if row.closure_date and not closed_90d and not closed_180d:
            closed_90d = True
            closed_180d = True

        records.append({
            "id":              str(row.id),
            "baseline_score":  row.baseline_score or 0,
            "risk_band":       row.baseline_risk_band or "unknown",
            "factors":         factors,
            "closed_90d":      closed_90d,
            "closed_180d":     closed_180d,
            "outcome_90d":     row.outcome_90d,
            "outcome_180d":    row.outcome_180d,
            "closure_date":    str(row.closure_date) if row.closure_date else None,
        })

    return records


def _is_closed_outcome(outcome: Optional[str]) -> bool:
    return outcome in ("closed_permanently", "closed_temporarily")


# ── Band statistics ───────────────────────────────────────────────────────────

def _compute_band_stats(records: list[dict]) -> dict:
    """Compute closure rates and average scores per risk band."""
    counts:       dict[str, int]   = defaultdict(int)
    closed_90d:   dict[str, int]   = defaultdict(int)
    closed_180d:  dict[str, int]   = defaultdict(int)
    score_sums:   dict[str, float] = defaultdict(float)

    for rec in records:
        band = rec["risk_band"]
        counts[band]    += 1
        score_sums[band] += rec["baseline_score"]
        if rec["closed_90d"]:
            closed_90d[band] += 1
        if rec["closed_180d"]:
            closed_180d[band] += 1

    stats = {}
    for band in _BANDS:
        n = counts.get(band, 0)
        if n == 0:
            continue
        stats[band] = {
            "count":          n,
            "closed_90d":     closed_90d.get(band, 0),
            "closed_180d":    closed_180d.get(band, 0),
            "closure_rate_90d":   round(closed_90d.get(band, 0) / n * 100, 1),
            "closure_rate_180d":  round(closed_180d.get(band, 0) / n * 100, 1),
            "avg_score":      round(score_sums[band] / n, 1),
        }

    return stats


# ── Overall model metrics ─────────────────────────────────────────────────────

def _compute_overall_metrics(records: list[dict]) -> dict:
    """Compute precision, recall, and AUC-ROC for the 180-day closure outcome."""
    at_risk_labels = {"elevated", "high"}

    tp = fp = fn = tn = 0
    for rec in records:
        predicted_at_risk = rec["risk_band"] in at_risk_labels
        actually_closed   = rec["closed_180d"]

        if predicted_at_risk and actually_closed:      tp += 1
        elif predicted_at_risk and not actually_closed: fp += 1
        elif not predicted_at_risk and actually_closed: fn += 1
        else:                                           tn += 1

    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else None
    recall    = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else None
    auc_roc   = _compute_auc_roc(records)

    return {
        "true_positives":  tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives":  tn,
        "precision_pct":   precision,
        "recall_pct":      recall,
        "auc_roc":         auc_roc,
        "threshold_description": "elevated or high risk (score <= 59)",
    }


def _compute_auc_roc(records: list[dict]) -> Optional[float]:
    """Compute AUC-ROC using the trapezoidal rule (no scikit-learn dependency).

    Uses baseline_score inverted as a classifier (lower score = higher risk).
    """
    if len(records) < 2:
        return None

    positives = [r for r in records if r["closed_180d"]]
    negatives = [r for r in records if not r["closed_180d"]]

    if not positives or not negatives:
        return None

    # U-statistic: fraction of (pos, neg) pairs where pos has lower score.
    concordant = 0
    ties = 0
    for p in positives:
        for n in negatives:
            if p["baseline_score"] < n["baseline_score"]:
                concordant += 1
            elif p["baseline_score"] == n["baseline_score"]:
                ties += 0.5

    auc = (concordant + ties) / (len(positives) * len(negatives))
    return round(float(auc), 3)


# ── Signal importance ─────────────────────────────────────────────────────────

def _compute_signal_importance(records: list[dict]) -> list[dict]:
    """Rank scoring components by how much lower they were in closed vs open restaurants."""
    closed    = [r for r in records if r["closed_180d"]]
    open_rest = [r for r in records if not r["closed_180d"]]

    if not closed or not open_rest:
        return []

    components = [
        "review_velocity_score",
        "rating_trend_score",
        "operational_score",
        "financial_risk_score",
        "health_component",
        "tabc_component",
    ]

    importance = []
    for comp in components:
        closed_vals = [r["factors"].get(comp, 0) for r in closed if comp in r["factors"]]
        open_vals   = [r["factors"].get(comp, 0) for r in open_rest if comp in r["factors"]]

        if not closed_vals or not open_vals:
            continue

        closed_avg = round(sum(closed_vals) / len(closed_vals), 1)
        open_avg   = round(sum(open_vals)   / len(open_vals),   1)
        delta      = round(open_avg - closed_avg, 1)  # positive = closed was lower

        importance.append({
            "signal":     comp,
            "avg_closed": closed_avg,
            "avg_open":   open_avg,
            "delta":      delta,
        })

    # Sort by delta descending — the signal that most distinguishes closed from open.
    importance.sort(key=lambda x: x["delta"], reverse=True)
    return importance


# ── Plain English summary ─────────────────────────────────────────────────────

def _build_plain_english_summary(
    records: list[dict],
    band_stats: dict,
    overall: dict,
    signal_importance: list[dict],
) -> str:
    total = len(records)
    if total == 0:
        return "No backtesting data available yet."

    # Closure rate at high/elevated risk.
    at_risk_closed  = sum(1 for r in records if r["risk_band"] in ("elevated", "high") and r["closed_180d"])
    at_risk_total   = sum(1 for r in records if r["risk_band"] in ("elevated", "high"))
    low_risk_closed = sum(1 for r in records if r["risk_band"] in ("low", "moderate") and r["closed_180d"])
    low_risk_total  = sum(1 for r in records if r["risk_band"] in ("low", "moderate"))

    # Relative risk.
    at_risk_rate  = (at_risk_closed  / at_risk_total  if at_risk_total  > 0 else 0)
    low_risk_rate = (low_risk_closed / low_risk_total if low_risk_total > 0 else 0.001)
    risk_multiplier = round(at_risk_rate / low_risk_rate, 1) if low_risk_rate > 0 else "N/A"

    # % of closures that were flagged.
    total_closures = sum(1 for r in records if r["closed_180d"])
    flagged_closures = at_risk_closed
    flagged_pct = round(flagged_closures / total_closures * 100) if total_closures > 0 else 0

    # Top 3 predictive signals.
    top_signals = [s["signal"].replace("_score", "").replace("_", " ") for s in signal_importance[:3]]
    top_signals_str = ", ".join(top_signals) if top_signals else "insufficient data"

    precision = overall.get("precision_pct")
    recall    = overall.get("recall_pct")

    lines = [
        f"Of {total} DFW restaurants backtested:",
        f"- Restaurants scoring below 60 were {risk_multiplier}x more likely to close within 180 days",
        f"- {flagged_pct}% of closures were predicted by a score below 60 at T-90",
        f"- Top predictive signals: {top_signals_str}",
    ]
    if precision is not None:
        lines.append(f"- Model precision at high risk threshold: {precision}%")
    if recall is not None:
        lines.append(f"- Model recall at high risk threshold: {recall}%")

    return "\n".join(lines)


# ── File output ───────────────────────────────────────────────────────────────

def _save_report(report: dict) -> None:
    """Write report JSON to /backtesting_results/report_{date}.json."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    path = _OUTPUT_DIR / f"report_{today_str}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("[accuracy] Report saved to %s", path)
