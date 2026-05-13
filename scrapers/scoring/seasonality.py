"""DFW restaurant industry seasonal adjustment factors and normalization functions."""

from typing import Optional

# DFW monthly traffic index relative to annual average (1.0 = average month).
# January is slowest (-20%); October/December are peaks (+15%).
SEASONAL_FACTORS = {
    1:  0.80,   # January   -20%
    2:  0.90,   # February  -10%
    3:  1.05,   # March      +5%
    4:  1.10,   # April     +10%
    5:  1.10,   # May       +10%
    6:  1.05,   # June       +5%
    7:  0.95,   # July       -5%
    8:  0.90,   # August    -10%
    9:  1.05,   # September  +5%
    10: 1.15,   # October   +15%
    11: 1.10,   # November  +10%
    12: 1.15,   # December  +15%
}


def normalize_volume(count: float, month: int) -> float:
    """Return seasonally adjusted review count for the given calendar month.

    Divides by the seasonal factor so that a high-traffic month's count is
    scaled down to its "expected" level — making it comparable across months.
    """
    factor = SEASONAL_FACTORS.get(month, 1.0)
    return count / factor


def compare_periods(
    current_counts: dict[int, int],
    prior_counts: dict[int, int],
) -> dict:
    """Compare two periods of monthly review counts with seasonal normalization.

    Args:
        current_counts: {month_int: review_count} for the current period.
        prior_counts:   {month_int: review_count} for the prior period.

    Returns a dict with:
        pct_change          – percentage change after adjustment (positive = growth)
        trend               – 'growing', 'stable', 'declining', 'sharply_declining'
        seasonally_adjusted – True (always True when this function is used)
        comparison_method   – 'period_comparison'
    """
    def _adj_total(counts: dict[int, int]) -> float:
        # Sum seasonally adjusted counts across all months in the period.
        return sum(normalize_volume(v, m) for m, v in counts.items())

    current_adj = _adj_total(current_counts)
    prior_adj   = _adj_total(prior_counts)

    if prior_adj == 0:
        pct_change = 0.0
    else:
        pct_change = (current_adj - prior_adj) / prior_adj * 100.0

    if pct_change > 20:
        trend = "growing"
    elif pct_change >= -20:
        trend = "stable"
    elif pct_change >= -50:
        trend = "declining"
    else:
        trend = "sharply_declining"

    return {
        "pct_change": round(pct_change, 1),
        "trend": trend,
        "seasonally_adjusted": True,
        "comparison_method": "period_comparison",
    }


def year_over_year(
    month_data: dict[tuple[int, int], int],
    target_month: int,
    target_year: int,
) -> Optional[dict]:
    """Compare the same calendar month this year vs last year.

    Args:
        month_data: {(year, month): review_count} covering at least 12 months.
        target_month: the month number (1-12) to compare.
        target_year:  the current year (e.g. 2026).

    Returns the same shape as compare_periods but with comparison_method
    'year_over_year', or None if prior-year data is missing (caller should
    fall back to compare_periods).
    """
    prior_year = target_year - 1
    current_count = month_data.get((target_year, target_month))
    prior_count   = month_data.get((prior_year, target_month))

    if current_count is None or prior_count is None:
        return None  # caller falls back to period comparison

    factor = SEASONAL_FACTORS.get(target_month, 1.0)
    current_adj = current_count / factor
    prior_adj   = prior_count  / factor

    if prior_adj == 0:
        pct_change = 0.0
    else:
        pct_change = (current_adj - prior_adj) / prior_adj * 100.0

    if pct_change > 20:
        trend = "growing"
    elif pct_change >= -20:
        trend = "stable"
    elif pct_change >= -50:
        trend = "declining"
    else:
        trend = "sharply_declining"

    return {
        "pct_change": round(pct_change, 1),
        "trend": trend,
        "seasonally_adjusted": True,
        "comparison_method": "year_over_year",
    }
