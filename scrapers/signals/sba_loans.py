"""SBA loan history scraper.

Downloads the SBA 7(a) FOIA CSV dataset from data.sba.gov and stream-parses it
to find loans for a specific restaurant. No API key required.

The SBA CKAN instance stores these as downloadable CSV files rather than
queryable datastore resources, so we stream the 150 MB CSV and filter by zip code
in one pass rather than loading it into memory — O(rows_in_zip) space, not O(file).

Fuzzy matching note for C# developers:
  fuzz.token_sort_ratio() from thefuzz is equivalent to FuzzySharp's
  Fuzz.TokenSortRatio(). Both tokenize, sort alphabetically, then compare —
  making them insensitive to word order. Score 80+ is our match threshold.

Risk signals produced:
  - CHGOFF (charged off / defaulted): high risk
  - 2+ SBA loans in history:          moderate risk (repeated distress borrowing)
"""

import csv
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from thefuzz import fuzz

logger = logging.getLogger(__name__)

_SBA_CKAN_BASE   = "https://data.sba.gov/api/3/action"
_SBA_PACKAGE_ID  = "7-a-504-foia"
_FUZZY_MIN_SCORE = 80
_TIMEOUT         = 60.0

# Cache the 150 MB CSV in /tmp so it is downloaded once per weekly run,
# not once per restaurant. TTL of 25 hours covers the weekly Sunday window.
_CACHE_PATH = Path("/tmp/sba_7a_foia.csv")
_CACHE_TTL  = 25 * 3600  # seconds

# LoanStatus codes in the SBA FOIA CSV.
_STATUS_MAP: dict[str, str] = {
    "P I F":   "paid_in_full",
    "CHGOFF":  "charged_off",
    "CANCLD":  "cancelled",
    "CANCELD": "cancelled",
    "EXEMPT":  "active",
}


def scrape_sba_loans(name: str, zip_code: str, restaurant_id: str, session: Session) -> dict:
    """Fetch SBA loan history for a restaurant by name and zip code.

    Writes matched loan records to raw_signals with source='sba_loans'.
    Returns the payload dict so callers can log or assert on it.
    """
    logger.info("SBA loans scrape: name=%r zip=%s", name, zip_code)

    csv_path = _ensure_csv_cached()
    if csv_path is None:
        logger.warning("No SBA CSV available — returning no_data_available")
        return _write_no_data(name, zip_code, restaurant_id, session, reason="api_unavailable")

    matched: list[dict] = []

    try:
        records = _parse_csv_file(csv_path, name, zip_code)
        matched.extend(records)
    except Exception as exc:
        logger.warning("SBA CSV parse failed: %s", exc)

    # Deduplicate by (borrname, approvaldate, grossapproval).
    # Python set of tuples — equivalent to HashSet<(string,string,string)> in C#.
    seen_keys: set[tuple] = set()
    unique_loans: list[dict] = []
    for loan in matched:
        key = (
            loan.get("BorrowerName", ""),
            loan.get("ApprovalDate", ""),
            str(loan.get("GrossApproval", "")),
        )
        if key not in seen_keys:
            seen_keys.add(key)
            unique_loans.append(loan)

    # Sort most recent first so index 0 is the latest loan.
    unique_loans.sort(key=lambda l: l.get("ApprovalDate", ""), reverse=True)

    has_chargeoff      = any(l.get("LoanStatus") == "CHGOFF" for l in unique_loans)
    repeated_borrowing = len(unique_loans) >= 2

    if has_chargeoff:
        risk_flag = "high_risk"
    elif repeated_borrowing:
        risk_flag = "moderate_risk"
    elif unique_loans:
        risk_flag = "none"
    else:
        risk_flag = "none_found"

    sba_latest_status: str          = "none_found"
    sba_latest_amount: Optional[float] = None
    if unique_loans:
        latest     = unique_loans[0]
        raw_status = (latest.get("LoanStatus") or "").strip()
        sba_latest_status = _STATUS_MAP.get(raw_status, "active")
        try:
            sba_latest_amount = float(latest.get("GrossApproval") or 0) or None
        except (TypeError, ValueError):
            sba_latest_amount = None

    payload = {
        "name_query":         name,
        "zip_query":          zip_code,
        "matched_loans":      unique_loans,
        "loan_count":         len(unique_loans),
        "risk_flag":          risk_flag,
        "has_chargeoff":      has_chargeoff,
        "repeated_borrowing": repeated_borrowing,
        "sba_latest_status":  sba_latest_status,
        "sba_latest_amount":  sba_latest_amount,
    }

    return _write_signal(restaurant_id, "sba_loans", payload, session)


def _ensure_csv_cached() -> Optional[Path]:
    """Download the most recent SBA 7(a) CSV to /tmp if absent or stale.

    Returns the local path on success, None on failure.

    Caching avoids re-downloading 150 MB per restaurant when the scheduler runs
    the job for all tracked restaurants in one pass. The 25-hour TTL covers the
    entire Sunday job window without keeping a stale file across multiple weeks.

    In C# you'd use IMemoryCache or a file-based approach in IHostedService;
    here a module-level Path constant acts as the cache key.
    """
    if _CACHE_PATH.exists():
        age = time.time() - _CACHE_PATH.stat().st_mtime
        if age < _CACHE_TTL:
            logger.info("SBA CSV cache hit (age %.0fs) — skipping download", age)
            return _CACHE_PATH
        logger.info("SBA CSV cache expired (age %.0fs) — re-downloading", age)

    url = _discover_latest_csv_url()
    if not url:
        return None

    logger.info("Downloading SBA 7(a) CSV → %s", _CACHE_PATH)
    try:
        with httpx.stream("GET", url, timeout=_TIMEOUT, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(_CACHE_PATH, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)
        size_mb = _CACHE_PATH.stat().st_size / 1_000_000
        logger.info("SBA CSV downloaded: %.1f MB", size_mb)
        return _CACHE_PATH
    except Exception as exc:
        logger.warning("SBA CSV download failed: %s", exc)
        _CACHE_PATH.unlink(missing_ok=True)
        return None


def _discover_latest_csv_url() -> Optional[str]:
    """Find the download URL for the FY2020-Present 7(a) CSV from the CKAN package."""
    try:
        resp = httpx.get(
            f"{_SBA_CKAN_BASE}/package_show",
            params={"id": _SBA_PACKAGE_ID},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            resources = data.get("result", {}).get("resources", [])
            # Prefer the "Present" (most recent) 7(a) CSV — most likely to contain
            # a restaurant's current or recent loan.
            for r in resources:
                name = r.get("name", "")
                if r.get("format", "").upper() == "CSV" and "7(a)" in name and "Present" in name:
                    url = r.get("url")
                    logger.info("Using SBA CSV: %s", name)
                    return url
    except Exception as exc:
        logger.warning("SBA package_show failed: %s", exc)
    return None


def _parse_csv_file(csv_path: Path, name: str, zip_code: str) -> list[dict]:
    """Read the cached CSV and return fuzzy-matched rows for this zip + name.

    Reads line by line so only zip-matching rows are held in memory —
    equivalent to C#'s StreamReader.ReadLine() in a while loop.
    """
    zip5 = (zip_code or "")[:5]
    matched: list[dict] = []

    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_zip = (row.get("borrzip") or "").strip()[:5]
            if row_zip != zip5:
                continue

            borrower = (row.get("borrname") or "").strip()
            score    = fuzz.token_sort_ratio(name.upper(), borrower.upper())
            if score < _FUZZY_MIN_SCORE:
                continue

            matched.append({
                "BorrowerName":        borrower,
                "LoanStatus":          (row.get("loanstatus") or "").strip(),
                "GrossApproval":       row.get("grossapproval"),
                "ApprovalDate":        (row.get("approvaldate") or "")[:10],
                "InitialInterestRate": row.get("initialinterestrate"),
                "TermInMonths":        row.get("terminmonths"),
                "ChargeOffDate":       (row.get("chargeoffdate") or "")[:10] or None,
                "BorrowerZip":         row.get("borrzip"),
                "_fuzzy_match_score":  score,
            })

    logger.info("SBA CSV parse: name=%r zip=%s → %d match(es)", name, zip5, len(matched))
    return matched


def _write_no_data(
    name: str,
    zip_code: str,
    restaurant_id: str,
    session: Session,
    reason: str = "no_data_available",
) -> dict:
    payload = {
        "status":             "no_data_available",
        "reason":             reason,
        "name_query":         name,
        "zip_query":          zip_code,
        "matched_loans":      [],
        "loan_count":         0,
        "risk_flag":          "none_found",
        "has_chargeoff":      False,
        "repeated_borrowing": False,
        "sba_latest_status":  "none_found",
        "sba_latest_amount":  None,
    }
    return _write_signal(restaurant_id, "sba_loans", payload, session)


def _write_signal(restaurant_id: str, source: str, payload: dict, session: Session) -> dict:
    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source":        source,
            "payload":       json.dumps(payload),
        },
    )
    session.commit()
    return payload
