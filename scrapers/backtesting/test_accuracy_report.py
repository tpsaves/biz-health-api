"""Test: Accuracy Report.

Runs accuracy report against all available retrospective data, prints the
full metrics table, and saves report JSON to /backtesting_results/.

Usage (from inside the scrapers container):
    python backtesting/test_accuracy_report.py
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.accuracy_report import generate_accuracy_report

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


def _print_section(title: str) -> None:
    print(f"\n── {title} ──")


def main():
    engine = _build_engine()

    print("\n" + "=" * 60)
    print("ACCURACY REPORT — Test Run")
    print("=" * 60)

    with Session(engine) as session:
        report = generate_accuracy_report(session)

    print(f"\nGenerated: {report['generated_at']}")
    print(f"Total restaurants backtested: {report['total_backtested']}")

    # Risk band breakdown table.
    _print_section("Risk Band Breakdown")
    header = f"  {'Band':<15}  {'Count':>6}  {'Closed 90d':>10}  {'Closed 180d':>12}  {'Avg Score':>10}"
    print(header)
    print("  " + "─" * 60)

    for band, stats in report.get("band_breakdown", {}).items():
        print(
            f"  {band:<15}  {stats['count']:>6}  "
            f"{stats['closure_rate_90d']:>9.1f}%  "
            f"{stats['closure_rate_180d']:>11.1f}%  "
            f"{stats['avg_score']:>10.1f}"
        )

    # Overall metrics.
    _print_section("Overall Model Performance")
    overall = report.get("overall", {})
    if overall.get("precision_pct") is not None:
        print(f"  Precision (180d): {overall['precision_pct']}%")
    else:
        print("  Precision (180d): insufficient data")
    if overall.get("recall_pct") is not None:
        print(f"  Recall    (180d): {overall['recall_pct']}%")
    else:
        print("  Recall    (180d): insufficient data")
    if overall.get("auc_roc") is not None:
        print(f"  AUC-ROC:          {overall['auc_roc']}")
    else:
        print("  AUC-ROC:          insufficient data")
    print(f"  TP={overall.get('true_positives',0)} FP={overall.get('false_positives',0)} "
          f"FN={overall.get('false_negatives',0)} TN={overall.get('true_negatives',0)}")

    # Signal importance.
    _print_section("Signal Importance (most predictive first)")
    print(f"  {'Signal':<30}  {'Avg (Closed)':>12}  {'Avg (Open)':>10}  {'Delta':>8}")
    print("  " + "─" * 65)
    for item in report.get("signal_importance", []):
        print(
            f"  {item['signal']:<30}  {item['avg_closed']:>12.1f}  "
            f"{item['avg_open']:>10.1f}  {item['delta']:>8.1f}"
        )

    # Plain English summary.
    _print_section("Plain English Summary (Customer-Ready)")
    print()
    for line in report.get("plain_english_summary", "").split("\n"):
        print(f"  {line}")

    # Confirm file saved.
    from pathlib import Path
    results_dir = Path("/backtesting_results")
    if results_dir.exists():
        files = sorted(results_dir.glob("report_*.json"), reverse=True)
        if files:
            print(f"\n── Report File ──")
            print(f"  {files[0]}")
            print(f"  ✓ Saved successfully")
        else:
            print("\n  ⚠ No report files found in /backtesting_results/")
    else:
        print("\n  ⚠ /backtesting_results/ directory not created")

    print()


if __name__ == "__main__":
    main()
