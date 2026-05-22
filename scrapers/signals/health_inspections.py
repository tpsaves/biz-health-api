"""Health inspection scraper supporting all 10 DFW cities.

Each city routes to the correct health authority:
  - Dallas         → City of Dallas (Dallas Open Data, Socrata)
  - Fort Worth     → Tarrant County Public Health (MyHealthDepartment portal)
  - Arlington      → Tarrant County Public Health (MyHealthDepartment portal)
  - Grand Prairie  → Tarrant County Public Health (MyHealthDepartment portal)
  - Plano          → Collin County (MyHealthDepartment portal)
  - Frisco         → Collin County (MyHealthDepartment portal)
  - McKinney       → Collin County (MyHealthDepartment portal)
  - Irving         → Dallas County Health (Dallas County portal)
  - Garland        → Dallas County Health (Dallas County portal)
  - Denton         → Denton County Public Health

Output schema is consistent regardless of source — the scoring engine needs no changes.
All records include 'program_identifier', 'insp_date', 'score', and '_health_authority'.
"""

import json
import logging
import os

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from scoring.signal_confidence import classify_inspection_confidence, get_place_types

logger = logging.getLogger(__name__)

# Dallas Open Data — historical inspections (Oct 2016 – Jan 2024, dataset dri5-wcct).
_DALLAS_OD_URL = "https://www.dallasopendata.com/resource/dri5-wcct.json"

# MyHealthDepartment portals — base URL pattern: {_MHD_BASE}/{region}/
_MHD_BASE = "https://inspections.myhealthdepartment.com"

# City → health authority routing table.
# handler: internal dispatcher key (see _fetch_records).
# authority: logged with each record for auditability.
_CITY_ROUTES: dict[str, dict] = {
    "dallas": {
        "authority": "City of Dallas",
        "handler": "dallas_opendata",
    },
    "fort worth": {
        "authority": "Tarrant County Public Health",
        "handler": "myhealthdept",
        "region": "tarrant",
    },
    "arlington": {
        "authority": "Tarrant County Public Health",
        "handler": "myhealthdept",
        "region": "tarrant",
    },
    "grand prairie": {
        "authority": "Tarrant County Public Health",
        "handler": "myhealthdept",
        "region": "tarrant",
    },
    "plano": {
        "authority": "Collin County Health Care Services",
        "handler": "myhealthdept",
        "region": "plano",
    },
    "frisco": {
        "authority": "Collin County Health Care Services",
        "handler": "myhealthdept",
        "region": "plano",
    },
    "mckinney": {
        "authority": "Collin County Health Care Services",
        "handler": "myhealthdept",
        "region": "plano",
    },
    "irving": {
        "authority": "Dallas County Health",
        "handler": "dallas_county",
    },
    "garland": {
        "authority": "Dallas County Health",
        "handler": "dallas_county",
    },
    "denton": {
        "authority": "Denton County Public Health",
        "handler": "denton_county",
    },
}


def scrape_inspections(name: str, city: str, restaurant_id: str, session: Session) -> dict:
    """Fetch health inspection records for any DFW city and write to raw_signals.

    Routes to the correct health authority automatically based on city.
    Writes no_inspection_data when the portal returns no results rather than raising,
    so a single missing city never blocks scoring for other signals.

    The `name` parameter is embedded in SoQL LIKE syntax. It comes from our own DB
    (not user input), so injection risk is low, but keep it out of external-facing code.
    """
    route = _CITY_ROUTES.get(city.lower())
    if route is None:
        logger.warning(
            "No health inspection route configured for city=%r — recording no_inspection_data",
            city,
        )
        return _write_no_data(name, city, "unconfigured_city", restaurant_id, session)

    authority = route["authority"]
    handler   = route["handler"]

    try:
        if handler == "dallas_opendata":
            records = _fetch_dallas_opendata(name)
        elif handler == "myhealthdept":
            records = _fetch_myhealthdept(name, route["region"], authority)
        elif handler == "dallas_county":
            records = _fetch_dallas_county(name, authority)
        elif handler == "denton_county":
            records = _fetch_denton_county(name, authority)
        else:
            records = []
    except Exception as exc:
        logger.warning(
            "Health inspection fetch failed city=%r authority=%r: %s",
            city, authority, exc,
        )
        records = []

    place_types = get_place_types(restaurant_id, session)

    if not records:
        return _write_no_data(name, city, authority, restaurant_id, session, place_types)

    confidence, confidence_reason, city_has_portal = classify_inspection_confidence(
        city, place_types, records
    )
    payload = {
        "records": records,
        "name_query": name,
        "city": city,
        "health_authority": authority,
        "inspection_confidence":        confidence,
        "inspection_confidence_reason": confidence_reason,
        "city_has_portal":              city_has_portal,
    }
    _write_signal(restaurant_id, "health_inspections", payload, session)

    latest = records[0]
    logger.info(
        "Stored health_inspections signal — name=%s authority=%s latest_score=%s date=%s",
        latest.get("program_identifier"),
        authority,
        latest.get("score"),
        (latest.get("insp_date") or "")[:10],
    )
    return payload


# ── per-authority fetch functions ──────────────────────────────────────────────

def _fetch_dallas_opendata(name: str) -> list[dict]:
    """Dallas Open Data Socrata SODA API (historical through Jan 2024)."""
    headers = {}
    app_token = os.environ.get("SODA_APP_TOKEN")
    if app_token:
        headers["X-App-Token"] = app_token

    resp = httpx.get(
        _DALLAS_OD_URL,
        params={
            # SoQL single-quote escaping: "Torchy's" → "TORCHY''S"
            "$where": (
                f"upper(program_identifier) like "
                f"'%{name.upper().replace(chr(39), chr(39) * 2)}%'"
            ),
            "$order": "insp_date DESC",
            "$limit": "10",
        },
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    records = resp.json()

    # Dallas Open Data records already match the canonical schema; just tag the authority.
    for r in records:
        r["_health_authority"] = "City of Dallas"
    return records


def _fetch_myhealthdept(name: str, region: str, authority: str) -> list[dict]:
    """MyHealthDepartment portal REST API (Tarrant and Collin counties).

    All MHD jurisdictions share a single POST endpoint at the domain root.
    The `path` field in the request body selects the jurisdiction (e.g. "tarrant").
    Response is a flat array of inspection records — the most recent inspection
    per matching establishment is returned directly (no nested inspections list).
    """
    headers = {
        # Required: portal returns 403 to non-browser User-Agent strings.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": _MHD_BASE,
        "Referer": f"{_MHD_BASE}/{region}/search",
    }

    body = {
        "task": "searchInspections",
        "data": {
            "path": region,
            "programName": "",
            "filters": {},
            "start": 0,
            "count": 10,
            "searchQueryOverride": None,
            "searchStr": name,
            "lat": 0,
            "lng": 0,
            "sort": {},
        },
    }

    resp = httpx.post(
        f"{_MHD_BASE}/",
        json=body,
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    records_raw = resp.json()

    if not isinstance(records_raw, list):
        return []

    # Normalize MHD field names to the canonical schema expected by the scoring engine.
    # MHD returns the most-recent inspection per establishment directly (not nested).
    records = []
    for item in records_raw:
        est_name = item.get("establishmentName", name)
        insp_date = (item.get("inspectionDate") or "")[:10]  # trim time component
        score = item.get("score")
        records.append({
            "program_identifier": est_name,
            "insp_date": insp_date,
            "score": str(score) if score is not None else "",
            "_health_authority": authority,
        })

    return records


def _fetch_dallas_county(name: str, authority: str) -> list[dict]:
    """Dallas County Health portal for Irving and Garland.

    Dallas County does not expose a documented public JSON API. The portal is
    available at dallascounty.org but requires web scraping. Returning empty
    list here causes the caller to record no_inspection_data gracefully.
    If Dallas County publishes a Socrata or REST endpoint in the future,
    implement it here without changing the callers.
    """
    logger.info(
        "Dallas County portal (%s) does not have a public API — no_inspection_data for %r",
        authority, name,
    )
    return []


def _fetch_denton_county(name: str, authority: str) -> list[dict]:
    """Denton County Public Health portal.

    Denton County does not expose a documented public JSON API at this time.
    Returns empty list so the caller records no_inspection_data gracefully.
    """
    logger.info(
        "Denton County portal (%s) does not have a public API — no_inspection_data for %r",
        authority, name,
    )
    return []


# ── shared helpers ─────────────────────────────────────────────────────────────

def _write_no_data(
    name: str,
    city: str,
    authority: str,
    restaurant_id: str,
    session: Session,
    place_types: list[str] | None = None,
) -> dict:
    """Write a no_inspection_data placeholder so the absence is explicit in the DB."""
    if place_types is None:
        place_types = get_place_types(restaurant_id, session)
    confidence, confidence_reason, city_has_portal = classify_inspection_confidence(
        city, place_types, []
    )
    payload = {
        "records": [],
        "name_query": name,
        "city": city,
        "health_authority": authority,
        "status": "no_inspection_data",
        "inspection_confidence":        confidence,
        "inspection_confidence_reason": confidence_reason,
        "city_has_portal":              city_has_portal,
    }
    _write_signal(restaurant_id, "health_inspections", payload, session)
    logger.info("no_inspection_data — name=%r city=%r authority=%r confidence=%r", name, city, authority, confidence)
    return payload


def _write_signal(restaurant_id: str, source: str, payload: dict, session: Session) -> None:
    session.execute(
        text(
            """
            INSERT INTO raw_signals (restaurant_id, source, payload)
            VALUES (:restaurant_id, :source, CAST(:payload AS jsonb))
            """
        ),
        {
            "restaurant_id": restaurant_id,
            "source": source,
            "payload": json.dumps(payload),
        },
    )
    session.commit()
