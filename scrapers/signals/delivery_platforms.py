"""DoorDash and Uber Eats listing status checker.

Checks whether a restaurant is actively listed on delivery platforms by querying
their public consumer-facing search endpoints. No API key required.

HTTP scraping note for C# developers:
  httpx.Client() is the Python equivalent of C#'s HttpClient — connection-pooled,
  reusable, handles redirects and cookies. Used as a context manager (with block)
  for automatic cleanup, like 'using var client = new HttpClient()' in C#.
  Unlike C# where async/await dominates, httpx is used synchronously here because
  APScheduler job callbacks run in thread pool workers, not an async event loop.

Fuzzy matching note:
  fuzz.token_sort_ratio() from thefuzz is the same function used in sba_loans.py —
  tokenizes, sorts tokens alphabetically, then computes Levenshtein similarity.
  Score 80+ is the consistent project-wide threshold for a positive name match.

Platform availability:
  DoorDash and Uber Eats regularly block headless HTTP clients with 403/429/503.
  When blocked, status='platform_unavailable' is written rather than raising an
  exception — equivalent to catching HttpRequestException in C# and returning a
  safe default. platform_unavailable results are excluded from scoring adjustments.

Change detection:
  Each run compares the current result against the most recent prior raw_signal
  for the same restaurant. A platform dropping from listed:true to listed:false
  (when both snapshots are reachable) sets delisted_doordash or delisted_ubereats.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from thefuzz import fuzz

logger = logging.getLogger(__name__)

_FUZZY_MIN_SCORE = 80
_TIMEOUT         = 20.0  # shorter than sba_loans — live web calls, not large file downloads

# Browser-like headers reduce the chance of an immediate 403 rejection.
# Platforms fingerprint beyond headers (TLS fingerprint, request timing, etc.)
# so this is a best-effort reduction, not a guarantee.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT":             "1",
}

# DoorDash consumer mobile BFF — public store search used by their iOS/Android apps.
_DD_SEARCH_URL = "https://consumer-mobile-bff.doordash.com/v1/store_search"

# Uber Eats public search suggestions endpoint.
_UE_BASE_URL   = "https://www.ubereats.com"
_UE_SEARCH_URL = "https://www.ubereats.com/api/getSearchSuggestionsV1"

# HTTP status codes that indicate the platform blocked the request.
_BLOCKED_STATUS_CODES = {403, 429, 503, 451}


def scrape_delivery_platforms(
    name:          str,
    address:       str,
    city:          str,
    restaurant_id: str,
    session:       Session,
) -> dict:
    """Check DoorDash and Uber Eats listing status for a restaurant.

    Writes results to raw_signals with source='delivery_platforms'.
    Returns the payload dict so callers can print or assert on it.
    """
    logger.info("Delivery platforms scrape: name=%r city=%s", name, city)

    checked_at = datetime.now(timezone.utc).isoformat()

    dd_result          = _check_doordash(name, city)
    dd_result["checked_at"] = checked_at

    ue_result          = _check_ubereats(name, city)
    ue_result["checked_at"] = checked_at

    # ── Change detection vs previous signal ───────────────────────────────────
    # Read the most recent prior delivery_platforms signal for this restaurant.
    # If a platform was listed:true before but listed:false now (and both snapshots
    # are reachable), set the delisted flag. platform_unavailable means we cannot
    # determine delist — leave flag False rather than risk a false positive.
    delisted_doordash = False
    delisted_ubereats = False

    prev_row = session.execute(
        text(
            """
            SELECT payload
            FROM raw_signals
            WHERE restaurant_id = :rid AND source = 'delivery_platforms'
            ORDER BY scraped_at DESC
            LIMIT 1
            """
        ),
        {"rid": restaurant_id},
    ).one_or_none()

    if prev_row:
        prev    = prev_row.payload
        prev_dd = prev.get("doordash", {})
        prev_ue = prev.get("ubereats", {})

        # Only flag delist when both snapshots are conclusive (not platform_unavailable).
        dd_now_conclusive = dd_result.get("status") not in (None, "platform_unavailable")
        ue_now_conclusive = ue_result.get("status") not in (None, "platform_unavailable")

        if prev_dd.get("listed") is True and dd_result.get("listed") is False and dd_now_conclusive:
            delisted_doordash = True
            logger.info("Delist detected on DoorDash for restaurant_id=%s", restaurant_id)

        if prev_ue.get("listed") is True and ue_result.get("listed") is False and ue_now_conclusive:
            delisted_ubereats = True
            logger.info("Delist detected on Uber Eats for restaurant_id=%s", restaurant_id)

    payload = {
        "doordash":          dd_result,
        "ubereats":          ue_result,
        "delisted_doordash": delisted_doordash,
        "delisted_ubereats": delisted_ubereats,
    }

    _write_signal(restaurant_id, "delivery_platforms", payload, session)
    return payload


def _unavailable_result() -> dict:
    """Return a platform_unavailable result when the platform blocks the request."""
    return {
        "status":           "platform_unavailable",
        "listed":           None,
        "matched_name":     None,
        "fuzzy_score":      None,
        "delivery_available": None,
    }


def _not_listed_result(best_score: Optional[int] = None) -> dict:
    return {
        "status":           "not_listed",
        "listed":           False,
        "matched_name":     None,
        "fuzzy_score":      best_score,
        "delivery_available": False,
    }


def _check_doordash(name: str, city: str) -> dict:
    """Query DoorDash consumer mobile BFF search and return listing status.

    DoorDash mobile BFF response shape:
      {"store_search_response": {"stores": [{"name": ..., "status": {...}}, ...]}}
    Falls back to platform_unavailable on any HTTP or parse error.
    """
    query   = f"{name} {city}"
    headers = {
        **_BROWSER_HEADERS,
        # DD-APP-VERSION mimics the DoorDash iOS app version. The BFF accepts
        # requests without this header but is more likely to return results with it.
        "DD-APP-VERSION": "2.120.0",
        "DD-APP-NAME":    "consumer_mobile_bff",
    }

    try:
        with httpx.Client(headers=headers, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_DD_SEARCH_URL, params={"query": query, "limit": "20"})

            if resp.status_code in _BLOCKED_STATUS_CODES:
                logger.warning("DoorDash returned HTTP %s — platform_unavailable", resp.status_code)
                return _unavailable_result()

            resp.raise_for_status()
            data = resp.json()

    except httpx.HTTPStatusError as exc:
        logger.warning("DoorDash HTTP error %s — platform_unavailable", exc.response.status_code)
        return _unavailable_result()
    except Exception as exc:
        logger.warning("DoorDash request failed: %s — platform_unavailable", exc)
        return _unavailable_result()

    # The BFF may return results under different keys depending on the app version.
    stores = (
        data.get("store_search_response", {}).get("stores")
        or data.get("stores")
        or data.get("results")
        or []
    )

    if not stores:
        return _not_listed_result()

    # Fuzzy match each store name against the restaurant name.
    # fuzz.token_sort_ratio is order-insensitive: "Lodge Pecan" == "Pecan Lodge".
    best_match: Optional[dict] = None
    best_score  = 0

    for store in stores:
        store_name = store.get("name") or store.get("store_name") or ""
        score      = fuzz.token_sort_ratio(name.upper(), store_name.upper())
        if score > best_score:
            best_score  = score
            best_match  = store

    if best_score < _FUZZY_MIN_SCORE or best_match is None:
        return _not_listed_result(best_score)

    # Extract delivery availability — DoorDash uses various status field shapes.
    store_status      = best_match.get("status") or {}
    delivery_status   = (
        store_status.get("delivery_status")
        or store_status.get("asap_available")
        or store_status.get("open")
    )
    # Treat "closed" / explicit False as not delivering right now, but still listed.
    delivery_available = delivery_status not in (None, "closed", "unavailable", False, "CLOSED")

    return {
        "status":           "listed",
        "listed":           True,
        "matched_name":     best_match.get("name") or best_match.get("store_name"),
        "fuzzy_score":      best_score,
        "delivery_available": bool(delivery_available),
    }


def _check_ubereats(name: str, city: str) -> dict:
    """Query Uber Eats search suggestions and return listing status.

    Uber Eats requires a CSRF token obtained from the homepage session cookie.
    Two-step: GET homepage → extract cookie/token → GET search suggestions.
    Falls back to platform_unavailable if either step fails.

    Uber Eats search suggestions response shape:
      {"data": {"searchSuggestions": [{"title": ..., "type": "RESTAURANT"}, ...]}}
    """
    query = f"{name} {city}"

    try:
        with httpx.Client(
            headers=_BROWSER_HEADERS,
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            # Step 1: GET homepage to obtain session cookies.
            # Uber Eats embeds a CSRF token that most API endpoints require.
            # Using "x" as a default token — Uber Eats accepts it for read-only
            # search endpoints that don't mutate state.
            home_resp = client.get(_UE_BASE_URL)

            if home_resp.status_code in _BLOCKED_STATUS_CODES:
                logger.warning("Uber Eats homepage blocked HTTP %s", home_resp.status_code)
                return _unavailable_result()

            # Try to extract the CSRF token from the page HTML.
            # Uber Eats embeds it as a JSON property in a script tag.
            csrf_token = "x"
            m = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', home_resp.text)
            if m:
                csrf_token = m.group(1)
            else:
                # Also try the x-csrf-token cookie if set.
                csrf_cookie = client.cookies.get("csrftoken") or client.cookies.get("_csrf")
                if csrf_cookie:
                    csrf_token = csrf_cookie

            # Step 2: GET search suggestions.
            search_resp = client.get(
                _UE_SEARCH_URL,
                params={"localeCode": "en-US", "query": query},
                headers={
                    "x-csrf-token": csrf_token,
                    "Referer":      _UE_BASE_URL,
                    "Origin":       _UE_BASE_URL,
                },
            )

            if search_resp.status_code in _BLOCKED_STATUS_CODES:
                logger.warning("Uber Eats search blocked HTTP %s", search_resp.status_code)
                return _unavailable_result()

            search_resp.raise_for_status()
            data = search_resp.json()

    except httpx.HTTPStatusError as exc:
        logger.warning("Uber Eats HTTP error %s — platform_unavailable", exc.response.status_code)
        return _unavailable_result()
    except Exception as exc:
        logger.warning("Uber Eats request failed: %s — platform_unavailable", exc)
        return _unavailable_result()

    # Parse search suggestions — Uber Eats API response shape varies by region/version.
    # Try multiple known shapes defensively.
    suggestions = (
        (data.get("data") or {}).get("searchSuggestions")
        or (data.get("data") or {}).get("results")
        or (data.get("data") if isinstance(data.get("data"), list) else None)
        or data.get("results")
        or []
    )

    if not suggestions:
        return _not_listed_result()

    # Fuzzy match suggestion names. Suggestions may be restaurants, categories,
    # or addresses — filter to restaurant-type suggestions where possible.
    best_match: Optional[dict] = None
    best_score  = 0

    for suggestion in suggestions:
        # Prefer restaurant-typed suggestions; fall back to all if none match.
        sug_type = suggestion.get("type", "").upper()
        sug_name = (
            suggestion.get("title")
            or suggestion.get("displayText")
            or suggestion.get("name")
            or ""
        )
        if not sug_name:
            continue
        score = fuzz.token_sort_ratio(name.upper(), sug_name.upper())
        if score > best_score:
            best_score  = score
            best_match  = suggestion

    if best_score < _FUZZY_MIN_SCORE or best_match is None:
        return _not_listed_result(best_score)

    matched_name = (
        best_match.get("title")
        or best_match.get("displayText")
        or best_match.get("name")
    )

    return {
        "status":           "listed",
        "listed":           True,
        "matched_name":     matched_name,
        "fuzzy_score":      best_score,
        # If a restaurant appears in Uber Eats search suggestions, delivery is
        # implied — there is no separate delivery_available field in this response.
        "delivery_available": True,
    }


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
