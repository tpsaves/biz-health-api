Read CLAUDE.md for full project context before doing anything.
Build a comprehensive backtesting framework to validate the scoring model against real restaurant outcomes. This is Phase 10 — run both retrospective and prospective paths simultaneously.

Part 1 — Closed Restaurant Finder
Create /scrapers/backtesting/closed_restaurant_finder.py that:

Searches for permanently closed DFW restaurants from four sources:

Source 1 — Google Places

Query Google Places API for restaurants in each DFW city
Filter for businesses with business_status: CLOSED_PERMANENTLY
Extract: name, address, city, google_place_id, estimated closure date (last review date as proxy)

Source 2 — TABC Cancellations

Query Texas Open Data TABC dataset for licenses with status Cancelled or Expired in the last 24 months
Filter for DFW zip codes
Extract: business name, address, city, cancellation date

Source 3 — Yelp Permanently Closed

Query Yelp Fusion API for permanently closed restaurants in DFW cities
Extract: name, address, city, yelp_id
Note: if Yelp API is unavailable skip this source gracefully

Source 4 — Dallas OpenData failed inspections

Query Dallas OpenData for restaurants with inspection scores below 60 in the last 24 months
Cross-reference against Google Places to check if still open
Extract restaurants that had critical failures and are no longer showing active on Google
Deduplicate results across all four sources using fuzzy name matching (minimum score 85)
Write results to a new closed_restaurants table
Target: 100-200 closed DFW restaurants with known or estimated closure dates

Create /db/migrations/closed_restaurants.sql:
sqlCREATE TABLE closed_restaurants (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  varchar NOT NULL,
  address               varchar,
  city                  varchar,
  zip                   varchar,
  google_place_id       varchar,
  yelp_id               varchar,
  closure_date          date,
  closure_date_estimated boolean,  -- true if derived from last review date
  closure_source        varchar,   -- google, tabc, yelp, inspection_inference
  created_at            timestamptz DEFAULT now()
);
Create /scrapers/backtesting/test_closed_finder.py that:

Runs the finder for all 10 DFW cities
Prints count of closed restaurants found per source
Prints 10 sample records with name, city, closure date, and source
Confirms data landed in closed_restaurants table


Part 2 — Backtest Cohort Builder
Create /scrapers/backtesting/cohort_builder.py that:

Queries Google Places for active restaurants across all 10 DFW cities
Scores each restaurant using the existing scoring engine
Builds a cohort of 200-500 restaurants distributed across risk bands:

Low risk (80-100): ~150 restaurants
Moderate risk (60-79): ~150 restaurants
Elevated risk (40-59): ~100 restaurants
High risk (0-39): ~50 restaurants


To find lower-scoring restaurants use these targeted searches:

Dallas OpenData: restaurants with inspection scores below 70 in last 6 months
TABC: restaurants with suspended or recently reinstated licenses
Google Places: restaurants with ratings below 3.5 and fewer than 50 reviews


Writes cohort to backtest_cohort table with baseline score and date

Create /db/migrations/backtest_cohort.sql:
sqlCREATE TABLE backtest_cohort (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id         uuid REFERENCES restaurants(id),
  cohort_type           varchar,    -- retrospective, prospective
  baseline_score        int,
  baseline_risk_band    varchar,    -- low, moderate, elevated, high
  baseline_date         date,
  baseline_factors      JSONB,      -- full score breakdown at baseline
  outcome_90d           varchar,    -- open, reduced_hours, closed, unknown
  outcome_180d          varchar,
  outcome_90d_date      date,
  outcome_180d_date     date,
  closure_date          date,
  closure_source        varchar,
  notes                 text,
  created_at            timestamptz DEFAULT now()
);
Create /scrapers/backtesting/test_cohort_builder.py that:

Runs cohort builder for Dallas and Fort Worth only as initial test
Prints risk band distribution of cohort
Confirms at least 20 restaurants across all four risk bands
Confirms data landed in backtest_cohort table


Part 3 — Historical Signal Reconstructor
Create /scrapers/backtesting/historical_reconstructor.py that:

Accepts a restaurant from closed_restaurants table as input
Reconstructs what signals looked like at T-90 and T-180 before closure:

Review signals (T-90 and T-180)

Call Outscraper for the closed restaurant's Google Place ID
Filter reviews to only those published before the T-90 or T-180 date
Compute review_velocity_score and rating_trend_score using only pre-cutoff reviews
Apply seasonality adjustment using the actual months at T-90 and T-180

Health inspection signals

Query Dallas OpenData and other inspection portals for historical records
Use only inspection records dated before T-90 or T-180 cutoff
Compute inspection_trend using records available at that point in time

TABC signals

Query TABC dataset for license status history
Determine license status as of T-90 and T-180 dates
Score the restaurant at T-90 and T-180 using only historically available signals
Write results to backtest_cohort table with cohort_type = 'retrospective'
Store full score breakdown in baseline_factors JSONB field

Create /scrapers/backtesting/test_reconstructor.py that:

Runs historical reconstruction for 10 closed restaurants
Prints T-90 and T-180 scores for each
Compares scores to the known closure outcome
Prints which signals were most degraded at T-90




Part 4 — Outcome Tracker
Create /scrapers/backtesting/outcome_tracker.py that:

Reads all prospective cohort restaurants from backtest_cohort where outcome_90d or outcome_180d is null
For each restaurant checks current status:

Closure detection

Google Places: check business_status — OPERATIONAL, CLOSED_TEMPORARILY, CLOSED_PERMANENTLY
TABC: check if license has been cancelled or suspended since baseline date
Review gap: flag if no new reviews in 45+ days (recency_gap_alert)
Hours changes: check hours_monitor for significant reductions since baseline
Updates backtest_cohort outcome fields based on days since baseline_date:

If 90+ days since baseline: populate outcome_90d
If 180+ days since baseline: populate outcome_180d


Outcome values: open, reduced_hours, closed_temporarily, closed_permanently, unknown

Add to /scrapers/scheduler.py:

Outcome tracker: weekly Sunday 3:00 AM UTC

Create /scrapers/backtesting/test_outcome_tracker.py that:

Runs outcome tracker against the prospective cohort
Prints current status for each restaurant
Confirms outcome fields updated in backtest_cohort


Part 5 — Accuracy Report
Create /scrapers/backtesting/accuracy_report.py that:

Reads all retrospective cohort records with known closure outcomes
Computes the following metrics:

By risk band
Risk Band    | Count | Closed 90d | Closed 180d | Avg Score
Low (80-100) |  XXX  |    X%      |     X%      |   XX
Mod (60-79)  |  XXX  |    X%      |     X%      |   XX
Elev (40-59) |  XXX  |    X%      |     X%      |   XX
High (0-39)  |  XXX  |    X%      |     X%      |   XX
Overall model performance

Precision: of restaurants flagged high/elevated risk, what % closed within 180 days
Recall: of restaurants that closed, what % were flagged high/elevated risk
AUC-ROC score: overall model discrimination ability

Signal importance

For each signal component rank by predictive power:

Which signal was most degraded in restaurants that closed vs stayed open
Average score per component for closed vs open restaurants



Plain English summary suitable for a customer pitch:
Of XXX DFW restaurants backtested:
- Restaurants scoring below 60 were XX times more likely to close within 180 days
- XX% of closures were predicted by a score below 60 at T-90
- Top predictive signals: [1], [2], [3]
- Model precision at high risk threshold: XX%
- Model recall at high risk threshold: XX%
Create /scrapers/backtesting/test_accuracy_report.py that:

Runs accuracy report against all available retrospective data
Prints full metrics table
Prints plain English summary
Saves report as /backtesting_results/report_{date}.json


Part 6 — API Endpoint for Backtest Results
Add to the .NET API:

GET /api/v1/backtesting/summary — returns latest accuracy report in JSON
GET /api/v1/backtesting/cohort — returns paginated cohort with baseline scores and outcomes


Part 7 — Demo UI Backtest Tab
Add a Backtesting tab to /demo/index.html that:

Shows the plain English accuracy summary at the top
Shows a risk band breakdown table with closure rates per band
Shows a signal importance ranking
Shows prospective cohort size and days until 90-day outcomes are available
Updates automatically from the backtesting API endpoints
Designed to be shown during a customer demo to prove model validity


EF Core Migrations
Create EF Core migrations for both new tables:

closed_restaurants
backtest_cohort


Test Sequence
Run in this order:
docker-compose exec scrapers python backtesting/test_closed_finder.py
docker-compose exec scrapers python backtesting/test_cohort_builder.py
docker-compose exec scrapers python backtesting/test_reconstructor.py
docker-compose exec scrapers python backtesting/test_outcome_tracker.py
docker-compose exec scrapers python backtesting/test_accuracy_report.py
Success Condition

closed_restaurants table contains 50+ closed DFW restaurants with closure dates
backtest_cohort table contains 200+ restaurants across all four risk bands
Historical reconstruction produces T-90 and T-180 scores for at least 20 closed restaurants
Accuracy report produces a plain English summary with precision and recall metrics
Backtesting tab visible in demo UI showing model validation results
Outcome tracker scheduled and running weekly
