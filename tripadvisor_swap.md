Read CLAUDE.md for full project context before doing anything.
Replace Foursquare with TripAdvisor as the second rating signal source. Foursquare is not returning venue details on the current plan so cross-source rating validation is not working. This is a targeted swap — architecture stays the same.
1. Create /scrapers/signals/tripadvisor.py that:

Accepts a restaurant name and address as input
Calls the TripAdvisor Content API to find the matching location and fetch: name, rating, num_reviews, ranking, price_level, and cuisine types
Writes the full raw API response as JSONB to raw_signals with source set to tripadvisor
Extracts rating normalized to 0-5 scale for use in scoring engine
Handles no-match gracefully with a clear log message

2. Create /scrapers/signals/test_tripadvisor.py that:

Runs the scraper against Pecan Lodge Dallas
Prints the raw signal and extracted rating to console
Confirms data landed in raw_signals

3. Update /scrapers/scoring/engine.py to:

Replace all references to foursquare source with tripadvisor source
Keep cross_source_divergence logic identical — compare Google rating vs TripAdvisor rating normalized to 0-5 scale
Keep source_divergence flag behavior unchanged

4. Update /scrapers/scheduler.py to:

Replace Foursquare scraper job with TripAdvisor scraper job
Keep same schedule: daily at 2:30 AM UTC

5. Remove Foursquare references:

Remove or archive /scrapers/signals/foursquare.py
Remove foursquare_place_id from the onboarding lookup if it causes errors without a valid API
Keep foursquare_place_id column in the restaurants table — just stop populating it

6. Update .env.example to replace FOURSQUARE_API_KEY with TRIPADVISOR_API_KEY
7. Create /scrapers/signals/test_tripadvisor.py that:

Tests against all 5 onboarded DFW restaurants
Prints rating returned for each
Confirms source_divergence logic can now compare two real rating values

Test inside Docker:
docker-compose exec scrapers python signals/test_tripadvisor.py
docker-compose exec scrapers python scoring/test_engine_v4.py
Success condition: TripAdvisor returns a real rating for at least 4 of 5 restaurants, cross-source divergence flag is now computable with real data from both sources.