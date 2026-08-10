Read CLAUDE.md for full project context before doing anything.
Build the DoorDash and Uber Eats listing status checker as a new signal source. This is Phase 9.
1. Create /scrapers/signals/delivery_platforms.py that:

Accepts a restaurant name, address, and city as input
Checks DoorDash listing status by querying DoorDash's public search endpoint:

Search by restaurant name and city
Use fuzzy name matching (fuzz.token_sort_ratio, minimum score 80) to match results
Extract: listing status (active/unlisted), restaurant name as listed, delivery available flag


Checks Uber Eats listing status by querying Uber Eats' public search endpoint:

Search by restaurant name and city
Use fuzzy name matching (minimum score 80) to match results
Extract: listing status (active/unlisted), restaurant name as listed, delivery available flag


Writes raw results as JSONB to raw_signals with source set to delivery_platforms
Payload structure:
json{
  "doordash": {
    "listed": true,
    "matched_name": "Pecan Lodge",
    "fuzzy_score": 100,
    "delivery_available": true,
    "checked_at": "2026-05-16T10:00:00Z"
  },
  "ubereats": {
    "listed": true,
    "matched_name": "Pecan Lodge",
    "fuzzy_score": 95,
    "delivery_available": true,
    "checked_at": "2026-05-16T10:00:00Z"
  }
}

Handles no match gracefully — set listed: false rather than throwing an error
Handles rate limiting or blocked requests gracefully — log as platform_unavailable and continue

2. Add change detection logic to detect platforms a restaurant was previously listed on but is no longer:

Compare the current delivery_platforms raw_signal against the previous one for the same restaurant
If a platform shows listed: true in the previous signal but listed: false in the current signal set delisted: true for that platform
A restaurant that was on both platforms and dropped off one or both is a stronger distress signal than one never listed

3. Create /scrapers/signals/test_delivery_platforms.py that:

Tests against all onboarded DFW restaurants
Prints DoorDash and Uber Eats status for each restaurant
Prints fuzzy match score for each match
Flags any platform_unavailable results
Confirms data landed in raw_signals

4. Update /scrapers/scoring/engine.py to incorporate delivery platform signals into operational_score:

Read the latest delivery_platforms raw_signal for the restaurant
Compare with previous signal for change detection
Apply scoring adjustments:

Listed on both platforms: +5 points to operational_score
Listed on one platform only: no change
Listed on neither platform (but was listed before): -20 points, flag delivery_platform_loss: true
Listed on neither platform (never listed): -10 points
Platform data unavailable: no adjustment, no flag


Add delivery platform factors to score factors output:
json{
  "signal": "delivery_platforms",
  "label": "DoorDash listing",
  "value": "Active",
  "impact": "positive",
  "weight": "medium",
  "flag": null
},
{
  "signal": "delivery_platforms",
  "label": "Uber Eats listing",
  "value": "Active",
  "impact": "positive",
  "weight": "medium",
  "flag": null
}


5. Update health_scores table with new columns:
sqldoordash_listed           boolean,
ubereats_listed           boolean,
delivery_platform_count   int,       -- 0, 1, or 2
delivery_status           varchar,   -- active, partial, offline, never_listed, unknown
delivery_platform_loss    boolean,   -- true if was listed before but now offline
Create an EF Core migration for all new columns.
6. Update /demo/index.html to display delivery platform status in Level 2 drill-down under Operational Health:

Show DoorDash status with colored badge:

Green: Active
Red: Not listed
Gray: Unknown


Show Uber Eats status with same color scheme
Show delivery_platform_loss as a warning badge at the top of the score card if triggered
Add delivery platform icons or labels so lenders immediately recognize the platforms

7. Update /scrapers/scheduler.py to add:

Delivery platforms checker: weekly Monday 5:00 AM UTC (after health inspections and TABC)

8. Create /scrapers/scoring/test_engine_v7.py that:

Runs full scoring engine against all onboarded restaurants
Prints operational_score with and without delivery platform adjustment for each
Prints DoorDash and Uber Eats status for each restaurant
Confirms new columns populated in health_scores

Add comments explaining Python HTTP scraping patterns and how they differ from HttpClient usage in C#.
Test inside Docker:
docker-compose exec scrapers python signals/test_delivery_platforms.py
docker-compose exec scrapers python scoring/test_engine_v7.py
Success condition:

DoorDash and Uber Eats status returned for at least 4 of 5 originally onboarded restaurants
Change detection logic confirmed working (compare two snapshots)
delivery_platform_loss flag triggers correctly when a restaurant drops off a platform
Demo UI shows platform badges in Level 2 drill-down under Operational Health
delivery_platform_loss appears as warning badge when triggered
operational_score adjusts correctly based on platform listing status