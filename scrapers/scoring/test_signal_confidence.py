"""Tests for signal_confidence.py — TABC and inspection confidence classification.

Run inside Docker:
    docker-compose exec scrapers python scoring/test_signal_confidence.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scoring.signal_confidence import classify_tabc_confidence, classify_inspection_confidence

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_cases_passed = 0
_cases_failed = 0


def check(label: str, got, expected) -> None:
    global _cases_passed, _cases_failed
    if got == expected:
        print(f"  {PASS}  {label}")
        _cases_passed += 1
    else:
        print(f"  {FAIL}  {label}")
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")
        _cases_failed += 1


# ── classify_tabc_confidence ──────────────────────────────────────────────────

print("\n── classify_tabc_confidence ──")

# 1. Bar with no TABC record → expected_not_found
state, _ = classify_tabc_confidence(['bar', 'restaurant', 'food'], [])
check("Bar with no TABC → expected_not_found", state, 'expected_not_found')

# 2. Coffee shop (cafe/takeaway only) with no TABC record → not_applicable
state, _ = classify_tabc_confidence(['cafe', 'meal_takeaway'], [])
check("Cafe/takeaway with no TABC → not_applicable", state, 'not_applicable')

# 3. Restaurant (no alcohol indicators) with no TABC → unknown
state, _ = classify_tabc_confidence(['restaurant', 'food'], [])
check("Restaurant/food only with no TABC → unknown", state, 'unknown')

# 4. Restaurant with active TABC record → confirmed
state, _ = classify_tabc_confidence(
    ['bar', 'restaurant'],
    [{"aimslicensetype": "MB", "aimslicenseid": "12345"}],
)
check("Restaurant with TABC record → confirmed", state, 'confirmed')

# 5. Night club with no TABC → expected_not_found
state, _ = classify_tabc_confidence(['night_club', 'food'], [])
check("Night club with no TABC → expected_not_found", state, 'expected_not_found')

# 6. No place types, no TABC → unknown
state, _ = classify_tabc_confidence([], [])
check("No place types, no TABC → unknown", state, 'unknown')

# 7. Bakery only → not_applicable
state, _ = classify_tabc_confidence(['bakery'], [])
check("Bakery only, no TABC → not_applicable", state, 'not_applicable')


# ── classify_inspection_confidence ───────────────────────────────────────────

print("\n── classify_inspection_confidence ──")

# 1. Records found → confirmed
state, _, portal = classify_inspection_confidence('Dallas', ['restaurant', 'food'], [{"score": 95}])
check("Dallas restaurant with records → confirmed", state, 'confirmed')

# 2. Irving (no portal) with no records → unknown, city_has_portal=False
state, _, portal = classify_inspection_confidence('Irving', ['restaurant', 'food'], [])
check("Irving (no portal) → unknown", state, 'unknown')
check("Irving city_has_portal = False", portal, False)

# 3. Garland (no portal) → unknown
state, _, portal = classify_inspection_confidence('Garland', ['cafe', 'food'], [])
check("Garland (no portal) → unknown", state, 'unknown')

# 4. Denton (no portal) → unknown
state, _, portal = classify_inspection_confidence('Denton', ['restaurant'], [])
check("Denton (no portal) → unknown", state, 'unknown')

# 5. Dallas restaurant, no records, working portal → expected_not_found
state, _, portal = classify_inspection_confidence('Dallas', ['restaurant', 'food'], [])
check("Dallas restaurant, no records → expected_not_found", state, 'expected_not_found')
check("Dallas city_has_portal = True", portal, True)

# 6. Fort Worth with food type, no records → expected_not_found
state, _, portal = classify_inspection_confidence('Fort Worth', ['bar', 'restaurant'], [])
check("Fort Worth bar/restaurant, no records → expected_not_found", state, 'expected_not_found')

# 7. McKinney cafe, no records → expected_not_found
state, _, portal = classify_inspection_confidence('McKinney', ['cafe', 'bakery'], [])
check("McKinney cafe, no records → expected_not_found", state, 'expected_not_found')

# 8. Dallas, no place types → unknown (can't determine food service)
state, _, portal = classify_inspection_confidence('Dallas', [], [])
check("Dallas, no place types → unknown", state, 'unknown')


# ── Summary ───────────────────────────────────────────────────────────────────

total = _cases_passed + _cases_failed
print(f"\n{'─'*50}")
print(f"  {_cases_passed}/{total} passed", end="")
if _cases_failed:
    print(f"  ({_cases_failed} failed)")
    sys.exit(1)
else:
    print("  — all good")
print()
