"""Review analysis utilities for the scoring engine.

Operates on raw Outscraper review objects stored in raw_signals payloads.
All functions accept the reviews_data list and compute aggregates at score time —
never at scrape time. This keeps the scraper's job simple (fetch and store
everything) and the engine's job correct (fresh aggregates from raw data).
"""

from datetime import datetime, timedelta, timezone

KEYWORD_FLAGS: dict[str, list[str]] = {
    "operational_instability": [
        "closed early", "not open", "were closed", "closed when",
        "hours wrong", "closed randomly", "inconsistent hours",
    ],
    "ownership_change": [
        "new owner", "new management", "under new ownership",
        "new staff", "changed ownership", "different owner",
    ],
    "sanitation_risk": [
        "health code", "roaches", "cockroach", "rodent", "mice",
        "dirty", "filthy", "disgusting", "unsanitary", "bug",
    ],
    "quality_decline": [
        "not what it used to be", "gone downhill", "used to be better",
        "quality dropped", "not as good", "disappointing",
        "went downhill", "used to love",
    ],
    "financial_stress": [
        "prices went up", "price increase", "too expensive now",
        "portion smaller", "portions smaller", "raised prices",
        "not worth the price",
    ],
}


def analyze_keywords(reviews: list[dict], cutoff_days: int = 60) -> dict:
    """Scan review text from the last cutoff_days for known risk keywords.

    Args:
        reviews: List of Outscraper review dicts with 'review_text',
                 'review_timestamp', and optionally 'review_id'.
        cutoff_days: Only reviews newer than this many days are analyzed.

    Returns:
        {
          "category": {
            "match_count": N,
            "examples": ["phrase found", ...],   # up to 2
            "review_refs": [{"review_id": ..., "date": "YYYY-MM-DD"}, ...]
          },
          ...
        }
        Only categories with at least one match are included.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    recent_reviews = [r for r in reviews if _review_dt(r) is not None and _review_dt(r) >= cutoff]

    findings: dict = {}
    for category, phrases in KEYWORD_FLAGS.items():
        match_count = 0
        examples: list[str] = []
        review_refs: list[dict] = []

        for review in recent_reviews:
            text = (review.get("review_text") or "").lower()
            if not text:
                continue
            matched_phrases = [p for p in phrases if p in text]
            if matched_phrases:
                match_count += 1
                for p in matched_phrases:
                    if p not in examples and len(examples) < 2:
                        examples.append(p)
                dt = _review_dt(review)
                review_refs.append({
                    "review_id": review.get("review_id", ""),
                    "date": dt.strftime("%Y-%m-%d") if dt else "",
                })

        if match_count > 0:
            findings[category] = {
                "match_count": match_count,
                "examples":    examples,
                "review_refs": review_refs,
            }

    return findings


def compute_rating_distribution(reviews: list[dict]) -> dict:
    """Return star-rating distribution for the last 90 days and all fetched reviews.

    Returns:
        {
          "recent_90d": {"5": pct, "4": pct, ..., "1": pct, "count": N} | None,
          "lifetime":   {"5": pct, "4": pct, ..., "1": pct, "count": N} | None,
        }
    Sections are None when fewer than 5 reviews with ratings are available.
    """
    cutoff_90d = datetime.now(timezone.utc) - timedelta(days=90)
    recent: list[int] = []
    all_r:  list[int] = []

    for review in reviews:
        rating = review.get("review_rating")
        if not isinstance(rating, (int, float)):
            continue
        rating = int(round(float(rating)))
        if not 1 <= rating <= 5:
            continue
        all_r.append(rating)
        dt = _review_dt(review)
        if dt and dt >= cutoff_90d:
            recent.append(rating)

    def _dist(ratings: list[int]) -> dict | None:
        if len(ratings) < 5:
            return None
        n = len(ratings)
        return {str(star): round(ratings.count(star) / n * 100, 1) for star in range(5, 0, -1)} | {"count": n}

    return {"recent_90d": _dist(recent), "lifetime": _dist(all_r)}


def compute_response_rates(reviews: list[dict]) -> tuple[int | None, int | None]:
    """Return owner response rate (%) for the last 60 days and the prior 60-day window.

    Requires at least 5 reviews in each period; returns None for periods with
    insufficient data.

    Returns:
        (rate_recent, rate_prior) — integers 0-100 or None
    """
    now           = datetime.now(timezone.utc)
    cutoff_recent = now - timedelta(days=60)
    cutoff_prior  = now - timedelta(days=120)

    recent_total, recent_responded = 0, 0
    prior_total,  prior_responded  = 0, 0

    for review in reviews:
        dt = _review_dt(review)
        if dt is None:
            continue
        responded = bool(review.get("owner_answer"))
        if dt >= cutoff_recent:
            recent_total     += 1
            recent_responded += int(responded)
        elif dt >= cutoff_prior:
            prior_total     += 1
            prior_responded += int(responded)

    rate_recent = round(recent_responded / recent_total  * 100) if recent_total  >= 5 else None
    rate_prior  = round(prior_responded  / prior_total   * 100) if prior_total   >= 5 else None
    return rate_recent, rate_prior


def _review_dt(review: dict):
    """Return a timezone-aware datetime from a review dict, or None."""
    ts = review.get("review_timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            pass

    dt_str = review.get("review_datetime_utc", "")
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None
