Read CLAUDE.md for full project context before doing anything.
Add signal confidence classification to the TABC and health inspection scrapers so the scoring engine and demo UI can distinguish between "no record found because it doesn't apply" vs "no record found but one was expected." This is a targeted enhancement to eliminate ambiguity in the score drill-down.

1. Create /scrapers/scoring/signal_confidence.py
Defines the signal confidence classification system:
pythonCONFIDENCE_STATES = {
    'confirmed':           'Record found and matched',
    'not_applicable':      'Business type does not require this signal',
    'expected_not_found':  'Record expected based on business type but not found — potential risk',
    'unknown':             'Cannot determine whether record should exist'
}

# Google Place types that indicate alcohol service
ALCOHOL_INDICATORS = [
    'bar', 'night_club', 'liquor_store', 'brewery', 'winery'
]

# Google Place types that indicate food service requiring inspection
FOOD_SERVICE_INDICATORS = [
    'restaurant', 'food', 'meal_takeaway', 'meal_delivery',
    'cafe', 'bakery', 'bar', 'night_club'
]

# Google Place types that typically do NOT serve alcohol
NO_ALCOHOL_EXPECTED = [
    'meal_takeaway', 'cafe', 'bakery', 'fast_food'
]
Provide these functions:
classify_tabc_confidence(place_types, tabc_result)

If tabc_result is a valid active/expired/suspended record: return confirmed
If place_types include any ALCOHOL_INDICATORS and tabc_result is empty: return expected_not_found
If place_types are exclusively NO_ALCOHOL_EXPECTED and tabc_result is empty: return not_applicable
If place_types include restaurant or food but no alcohol indicators and tabc_result is empty: return unknown — restaurants may or may not serve alcohol
All other cases: return unknown

classify_inspection_confidence(city, place_types, inspection_result)

If inspection_result has records: return confirmed
If city is Irving, Garland, or Denton (no public API): return unknown — cannot determine
If place_types include any FOOD_SERVICE_INDICATORS and inspection_result is empty for a city with a working portal (Dallas, Fort Worth, Arlington, Plano, Frisco, McKinney): return expected_not_found
All other cases: return unknown


2. Update /scrapers/signals/tabc_license.py
After completing the TABC lookup:

Import classify_tabc_confidence from signal_confidence.py
Retrieve the restaurant's Google Place types from the restaurants table
Classify the confidence state
Add to the raw_signals payload:
json{
  "tabc_confidence": "expected_not_found",
  "tabc_confidence_reason": "Bar/restaurant with alcohol indicators but no TABC record found",
  "place_types_checked": ["bar", "restaurant", "food"]
}



3. Update /scrapers/signals/health_inspections.py
After completing the inspection lookup:

Import classify_inspection_confidence from signal_confidence.py
Retrieve the restaurant's Google Place types from the restaurants table
Classify the confidence state
Add to the raw_signals payload:
json{
  "inspection_confidence": "expected_not_found",
  "inspection_confidence_reason": "Food service establishment in Dallas with working portal but no inspection records found",
  "city_has_portal": true
}



4. Update health_scores table
Add new columns:
sqltabc_confidence              varchar,  -- confirmed, not_applicable, expected_not_found, unknown
tabc_confidence_reason       varchar,
inspection_confidence        varchar,  -- confirmed, not_applicable, expected_not_found, unknown
inspection_confidence_reason varchar,
Create EF Core migration for all new columns.

5. Update /scrapers/scoring/engine_v2.py
Read confidence classifications and apply scoring adjustments:
TABC confidence scoring:

confirmed (active license): existing scoring unchanged
confirmed (suspended/expired): existing caps unchanged
expected_not_found: apply -15 points to operational_score, flag tabc_expected_missing: true
not_applicable: no adjustment, no flag
unknown: no adjustment, no flag

Inspection confidence scoring:

confirmed: existing scoring unchanged
expected_not_found: apply -10 points to operational_score, flag inspection_expected_missing: true
unknown (city has no portal): no adjustment, no flag
unknown (city has portal): no adjustment, flag inspection_data_unavailable: true

Add new flags to health_scores:
sqltabc_expected_missing        boolean,
inspection_expected_missing  boolean,
inspection_data_unavailable  boolean,

6. Update score factors output
Replace the current "No record found" factor with confidence-aware output:
For expected_not_found:
json{
  "signal": "tabc_license",
  "label": "TABC license",
  "value": "Expected but not found — bar/restaurant with no active liquor license on record",
  "impact": "negative",
  "weight": "high",
  "flag": "tabc_expected_missing"
}
For not_applicable:
json{
  "signal": "tabc_license",
  "label": "TABC license",
  "value": "N/A — business type does not require a liquor license",
  "impact": "neutral",
  "weight": "none",
  "flag": null
}
For unknown:
json{
  "signal": "tabc_license",
  "label": "TABC license",
  "value": "No record found — unable to determine if license required",
  "impact": "neutral",
  "weight": "none",
  "flag": null
}

7. Update /demo/index.html
Update the TABC and inspection signal rows in Level 2 drill-down:

confirmed: existing display unchanged
expected_not_found: show red indicator with label "Expected — Not Found" and tooltip explaining why it was expected
not_applicable: show gray indicator with label "N/A" and tooltip "Business type does not require this"
unknown: show gray indicator with label "No Data" — same as current but no red warning

Add new warning badges:

tabc_expected_missing — red badge "License Missing"
inspection_expected_missing — orange badge "Inspection Records Missing"


8. Create test files
/scrapers/scoring/test_signal_confidence.py that:

Tests classify_tabc_confidence with all four scenarios:

Bar with no TABC record → expected_not_found
Coffee shop with no TABC record → not_applicable
Restaurant with no TABC record → unknown
Restaurant with active TABC → confirmed


Tests classify_inspection_confidence with all scenarios
Prints pass/fail for each case

Re-run /scrapers/scoring/test_engine_v7.py after changes to confirm:

Backyard Dallas now shows tabc_expected_missing: true (it has bar/restaurant types but no TABC)
operational_score adjusts accordingly
Score factors show "Expected but not found" instead of ambiguous "No record found"


Test Sequence
docker-compose exec scrapers python scoring/test_signal_confidence.py
docker-compose exec scrapers python scoring/test_engine_v7.py
Success condition:

All 4 TABC confidence scenarios pass in test_signal_confidence.py
Backyard Dallas shows tabc_expected_missing flag
Demo UI shows "Expected — Not Found" in red for Backyard Dallas TABC row
Coffee shops and non-alcohol restaurants show "N/A" instead of "No record found"
No healthy restaurant shows a false negative confidence flag