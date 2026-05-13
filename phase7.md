Read CLAUDE.md for full project context before doing anything.
This is Phase 7 with three goals: add Outscraper for deep Google review history, expand health inspection coverage to all DFW cities, and confirm TABC liquor license coverage works for all cities.
1. Add Outscraper as a supplemental review history source
Create /scrapers/signals/outscraper_reviews.py that:

Accepts a google_place_id and restaurant name as input
Calls the Outscraper Google Maps Reviews API to fetch up to 12 months of reviews sorted by date
Extracts monthly review counts for the last 12 months from review timestamps
Extracts average star rating per month for the last 12 months
Writes the full raw response as JSONB to raw_signals with source set to outscraper_reviews
Stores a monthly_breakdown key in the payload:
json{
  "monthly_breakdown": {
    "2025-05": { "count": 23, "avg_rating": 4.2 },
    "2025-06": { "count": 31, "avg_rating": 4.4 },
    ...
  }
}

Use OUTSCRAPER_API_KEY from .env

Create /scrapers/signals/test_outscraper_reviews.py that:

Runs against all 5 onboarded DFW restaurants
Prints monthly breakdown for each restaurant
Confirms data landed in raw_signals

2. Update /scrapers/scoring/engine.py to:

Check for outscraper_reviews signal before computing velocity metrics
If outscraper_reviews data exists use year_over_year() from seasonality.py with real monthly data
If outscraper_reviews data does not exist fall back to existing period comparison method
Update comparison_method field accordingly: year_over_year when Outscraper data available, period_comparison or insufficient_data otherwise
Remove insufficient_data suppression for monthly_volume_trend when real monthly data is available from Outscraper

3. Expand health inspection coverage
Update /scrapers/signals/health_inspections.py to support all DFW cities using the correct health authority per city:
CityHealth AuthorityPortal URLDallasCity of Dallasinspections.myhealthdepartment.com/dallasFort WorthTarrant County Public Healthinspections.myhealthdepartment.com/tarrantArlingtonTarrant County Public Healthinspections.myhealthdepartment.com/tarrantGrand PrairieTarrant County Public Healthinspections.myhealthdepartment.com/tarrantPlanoCity of Plano / Collin Countyinspections.myhealthdepartment.com/planoFriscoCollin Countyinspections.myhealthdepartment.com/planoMcKinneyCollin Countyinspections.myhealthdepartment.com/planoIrvingDallas County HealthDallas County portalGarlandDallas County HealthDallas County portalDentonDenton CountyDenton County portal
The scraper should:

Accept city as input and route to the correct health authority automatically
Use the same output schema for all cities so the scoring engine needs no changes
Log which health authority was used for each inspection record
Handle cities where the portal returns no results gracefully — log as no_inspection_data rather than throwing an error

Create /scrapers/signals/test_health_inspections_all_cities.py that:

Tests against one restaurant per city across all 10 cities
Prints inspection records found or no_inspection_data for each
Confirms routing logic is working correctly

4. Confirm TABC liquor license coverage for all DFW cities
Update /scrapers/signals/tabc_license.py to:

Remove any city-specific filtering that limits results to Dallas or Fort Worth
Confirm the Texas Open Data Portal TABC dataset covers all Texas cities including Arlington, Plano, Frisco, McKinney, Denton, Irving, Garland, and Grand Prairie
Add city to the license lookup query to improve match accuracy when a restaurant name appears in multiple cities
Log the city matched for each license record

Create /scrapers/signals/test_tabc_all_cities.py that:

Tests against one restaurant per city across all 10 cities
Prints license status and city matched for each
Confirms TABC data returns results for non-Dallas/Fort Worth cities

5. Update /scrapers/scheduler.py to:

Add outscraper_reviews scraper job running weekly on Sunday at 1:00 AM UTC
Weekly is sufficient — Outscraper is pay-per-use so minimize unnecessary calls
Outscraper job runs before the Sunday scoring engine run so fresh monthly data feeds into scores

6. Update the .env.example to add:
OUTSCRAPER_API_KEY=
7. Create /scrapers/scoring/test_engine_v5.py that:

Runs the full scoring engine against all 5 restaurants
Prints which comparison method was used for each (year_over_year vs period_comparison)
Confirms monthly_volume_trend now shows real trend data instead of insufficient_data for restaurants with Outscraper history

Add comments explaining Python-specific patterns that differ from C# where relevant.
Test inside Docker:
docker-compose exec scrapers python signals/test_outscraper_reviews.py
docker-compose exec scrapers python signals/test_health_inspections_all_cities.py
docker-compose exec scrapers python signals/test_tabc_all_cities.py
docker-compose exec scrapers python scoring/test_engine_v5.py
Success condition:

Outscraper returns 12-month review breakdown for at least 4 of 5 restaurants
Health inspections return data for at least 8 of 10 cities
TABC returns license data for restaurants in non-Dallas/Fort Worth cities
scoring engine shows year_over_year as comparison method for restaurants with Outscraper data
monthly_volume_trend shows real trend values instead of insufficient_data