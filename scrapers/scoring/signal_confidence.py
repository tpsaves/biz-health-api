"""Signal confidence classification for TABC and health inspection signals.

Eliminates ambiguity between "no record because not applicable" vs
"no record but one was expected" — the latter is a meaningful risk signal.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

CONFIDENCE_STATES = {
    'confirmed':          'Record found and matched',
    'not_applicable':     'Business type does not require this signal',
    'expected_not_found': 'Record expected based on business type but not found — potential risk',
    'unknown':            'Cannot determine whether record should exist',
}

ALCOHOL_INDICATORS = ['bar', 'night_club', 'liquor_store', 'brewery', 'winery']

FOOD_SERVICE_INDICATORS = [
    'restaurant', 'food', 'meal_takeaway', 'meal_delivery',
    'cafe', 'bakery', 'bar', 'night_club',
]

NO_ALCOHOL_EXPECTED = ['meal_takeaway', 'cafe', 'bakery', 'fast_food']

_CITIES_WITH_PORTALS = frozenset({
    'dallas', 'fort worth', 'arlington', 'grand prairie',
    'plano', 'frisco', 'mckinney',
})
_CITIES_WITHOUT_PORTALS = frozenset({'irving', 'garland', 'denton'})


def get_place_types(restaurant_id: str, session: Session) -> list[str]:
    """Read Google Place types from the latest google_places raw_signal."""
    row = session.execute(
        text(
            "SELECT payload FROM raw_signals "
            "WHERE restaurant_id = :rid AND source = 'google_places' "
            "ORDER BY scraped_at DESC LIMIT 1"
        ),
        {"rid": restaurant_id},
    ).one_or_none()
    if not row:
        return []
    return (row.payload.get("v1_raw") or {}).get("types", [])


def classify_tabc_confidence(
    place_types: list[str],
    tabc_result: list[dict],
) -> tuple[str, str]:
    """Classify TABC signal confidence.

    Returns (confidence_state, reason).
    """
    if tabc_result:
        return 'confirmed', 'TABC record found and matched'

    types = place_types or []

    if any(t in ALCOHOL_INDICATORS for t in types):
        return (
            'expected_not_found',
            'Bar/night club/brewery with alcohol indicators but no TABC record found',
        )

    food_types_present = [t for t in types if t in FOOD_SERVICE_INDICATORS]
    if food_types_present and all(t in NO_ALCOHOL_EXPECTED for t in food_types_present):
        return (
            'not_applicable',
            'Business type (cafe/bakery/takeaway) does not typically serve alcohol',
        )

    if any(t in ('restaurant', 'food') for t in types):
        return (
            'unknown',
            'Restaurant may or may not serve alcohol — cannot determine without license check',
        )

    return 'unknown', 'Cannot determine if TABC license is required'


def classify_inspection_confidence(
    city: str,
    place_types: list[str],
    inspection_result: list[dict],
) -> tuple[str, str, bool]:
    """Classify health inspection signal confidence.

    Returns (confidence_state, reason, city_has_portal).
    """
    if inspection_result:
        return 'confirmed', 'Inspection records found and matched', True

    city_lower = city.lower()

    if city_lower in _CITIES_WITHOUT_PORTALS:
        return (
            'unknown',
            f'{city} does not have a public inspection API — cannot determine',
            False,
        )

    types = place_types or []
    if any(t in FOOD_SERVICE_INDICATORS for t in types) and city_lower in _CITIES_WITH_PORTALS:
        return (
            'expected_not_found',
            f'Food service establishment in {city} with working portal but no inspection records found',
            True,
        )

    has_portal = city_lower in _CITIES_WITH_PORTALS
    return 'unknown', 'Cannot determine if inspection records should exist', has_portal
