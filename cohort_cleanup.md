Read CLAUDE.md for full project context before doing anything.
The prospective backtest cohort is contaminated — the majority of 242 businesses are liquor stores, food marts, and off-premise beer/wine retailers rather than restaurants. This needs to be cleaned before the 90-day outcome window closes in August.
Step 1 — Diagnose the contamination
Query the backtest_cohort table joined with restaurants to see what business types are present:
sqlSELECT r.name, r.city, bc.baseline_risk_band, bc.baseline_score
FROM backtest_cohort bc
JOIN restaurants r ON bc.restaurant_id = r.id
WHERE bc.cohort_type = 'prospective'
ORDER BY bc.baseline_risk_band, r.name
LIMIT 100;
Print the first 100 rows so we can see what types of businesses are in the cohort.
Step 2 — Build a restaurant classifier
Create /scrapers/backtesting/restaurant_classifier.py that:

Accepts a business name and Google Place ID as input
Uses Google Places API to check the business types array for the place
Classifies as RESTAURANT if types include any of:
restaurant, food, meal_takeaway, meal_delivery, bar, cafe, bakery, night_club
Classifies as NON_RESTAURANT if types include any of:
liquor_store, convenience_store, grocery_or_supermarket, food_store
OR if name contains keywords: liquor, spirits, wine & beer, food mart, convenience, deli mart,
off premise, package store, tobacco
Returns classification and confidence (high/medium/low)

Step 3 — Clean the cohort
Create /scrapers/backtesting/clean_cohort.py that:

Reads all prospective cohort restaurants
Runs each through the restaurant classifier
Removes NON_RESTAURANT businesses from backtest_cohort table
Also removes them from restaurants table if they were onboarded solely for backtesting
Prints a summary: how many removed, how many kept, breakdown by classification

Step 4 — Rebuild the elevated and high risk bands with real restaurants
After cleaning update cohort_builder.py to filter results more strictly:

Only include businesses where Google Places types contains restaurant or food
Exclude any business where name contains liquor, spirits, wine & beer, food mart,
convenience, package store, tobacco, off premise
Add a place_types field to the restaurant classifier check before onboarding
Re-run cohort builder targeting 100 restaurants across elevated and high risk bands
using only verified restaurant businesses

Step 5 — Report final clean cohort
Print:

Total prospective cohort after cleaning
Risk band distribution after cleaning
How many non-restaurant businesses were removed
How many new verified restaurants were added

Target after cleaning: 150+ verified restaurants across all 4 risk bands.
Test inside Docker:
docker-compose exec scrapers python backtesting/clean_cohort.py
docker-compose exec scrapers python backtesting/test_cohort_builder.py
Success condition: prospective cohort contains only verified restaurants, risk band
distribution maintained across all 4 bands, no liquor stores or food marts remaining.