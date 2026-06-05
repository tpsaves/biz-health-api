# Small Business Financial Health Signals API
## Vertical: Restaurants & Food Service (DFW Region)
## Product Name: Tableside API

## Project Overview
An API that aggregates public signals about restaurants and outputs a financial health score.
Targeted at food & beverage distributors, restaurant equipment lenders, and commercial landlords
who need to assess the risk of small restaurant businesses before extending credit or net terms.

## Architecture

### Services
| Service | Stack | Responsibility |
|---|---|---|
| `api` | .NET 9 Web API + EF Core | Serves health scores and restaurant data via REST |
| `scrapers` | Python 3.11 + SQLAlchemy + APScheduler | Collects and stores raw signals on a schedule |
| `db` | PostgreSQL 17 | Shared data store |
| `demo` | nginx:alpine serving /demo/index.html | Customer-facing demo UI at http://localhost:3000 |

### Project Structure
```
/biz-health-api
  docker-compose.yml
  .env
  CLAUDE.md
  /api
    Dockerfile
    BizHealthApi.sln
  /scrapers
    Dockerfile
    scheduler.py
    scheduler_config.py
    requirements.txt
    /signals
      google_places.py         # Google Places API v1 — stores full response including types
      foursquare.py            # Inactive — free tier returns no venue details
      health_inspections.py    # All 10 DFW cities — confidence classification added
      tabc_license.py          # Texas Open Data Portal — confidence classification added
      hours_monitor.py
      outscraper_reviews.py    # Primary review source — biweekly 1st/15th, stores FULL reviews_data
      outscraper_quota.py      # Monthly usage cap — 15,000 records/month with safety threshold
      sba_loans.py
      property_tax.py
      delivery_platforms.py    # platform_unavailable in Docker (bot detection)
    /scoring
      engine.py
      engine_v2.py             # Current active engine
      seasonality.py
      keyword_analyzer.py      # 5-category keyword detection from review text
      signal_confidence.py     # TABC and inspection confidence classification
    /onboarding
      restaurant_lookup.py
      bulk_onboard.py
    /backtesting
      closed_restaurant_finder.py
      cohort_builder.py
      historical_reconstructor.py
      outcome_tracker.py
      accuracy_report.py
      restaurant_classifier.py
      clean_cohort.py
      import_closed_restaurants.py
  /demo
    index.html
  /db
    init.sql
    /migrations
      outscraper_run_log.sql
  /backtesting_results
    report_{date}.json
```

### Docker Compose
- All services run via docker-compose
- Postgres hostname inside Docker network is `db`
- Postgres exposed to Windows host at `localhost:5432`
- API runs on port 8080
- Demo UI served via nginx at http://localhost:3000 (must use HTTP not file://)
- Scrapers container restarts automatically (restart: unless-stopped)

---

## Database

- **Engine**: PostgreSQL 17
- **ORM (.NET)**: EF Core with Npgsql provider
- **ORM (Python)**: SQLAlchemy
- **Connection string (.NET)**: Host=db;Port=5432;Database=bizhealth;Username=admin;Password=...
- **Connection string (Python)**: postgresql://admin:password@db:5432/bizhealth

### Key Tables

**restaurants** — name, address, city, state, zip, google_place_id, foursquare_place_id, phone, website

**raw_signals** — source, payload JSONB, scraped_at UTC

Sources: google_places, outscraper_reviews, foursquare (inactive), health_inspection,
tabc_license, hours_monitor, sba_loans, property_tax, delivery_platforms,
outscraper_quota_exceeded, {source}_error

**health_scores**
```sql
-- Score components
review_velocity_score     int,
rating_trend_score        int,
operational_score         int,
staffing_score            int,        -- null, placeholder for future job posting signal
financial_risk_score      int,
overall_score             int,

-- Trend analysis
license_history_risk      boolean,
inspection_trend          varchar,
hours_change_count        int,
last_inspection_date      date,
last_inspection_score     int,
license_status            varchar,
license_expiry_date       date,

-- Enhanced velocity and rating
review_gap_alert          boolean,
one_star_spike            boolean,
rating_deterioration      boolean,
source_divergence         boolean,
ninety_day_slope          varchar,
days_since_last_review    int,
owner_response_rate       int,
monthly_volume_trend      varchar,
review_count_confidence   varchar,
seasonality_adjusted      boolean,
comparison_method         varchar,
volume_trend_confidence   varchar,
recency_source            varchar,

-- Financial risk
sba_default               boolean,
repeated_sba_borrowing    boolean,
tax_delinquent            boolean,
sba_loan_count            int,
sba_latest_status         varchar,
sba_latest_amount         decimal,
tax_delinquency_years     int,

-- Delivery platforms
doordash_listed           boolean,
ubereats_listed           boolean,
delivery_platform_count   int,
delivery_status           varchar,
delivery_platform_loss    boolean,

-- Composite risk cap
composite_risk_cap        boolean,

-- Enhanced rating trend
pct_5star_recent          decimal,
pct_1star_recent          decimal,
high_negative_rate        boolean,
negative_rate_rising      boolean,
bimodal_distribution      boolean,
sanitation_flag           boolean,
operational_instability_flag boolean,
ownership_change_flag     boolean,
quality_decline_flag      boolean,
financial_stress_flag     boolean,
keyword_findings          JSONB,
response_rate_declining   boolean,
owner_disengaged          boolean,
response_rate_recent      int,
response_rate_prior       int,

-- Signal confidence
tabc_confidence              varchar,  -- confirmed, not_applicable, expected_not_found, unknown
tabc_confidence_reason       varchar,
inspection_confidence        varchar,
inspection_confidence_reason varchar,
tabc_expected_missing        boolean,
inspection_expected_missing  boolean,
inspection_data_unavailable  boolean,

-- Score factors
score_factors             JSONB,
scored_at                 timestamptz
```

**outscraper_usage**
```sql
id, restaurant_id, records_fetched, month (YYYY-MM), scraped_at
```

**outscraper_run_log**
```sql
id, run_date, status (ok/throttled/skipped),
restaurants_completed, restaurants_skipped,
records_used_before, records_used_after, created_at
```

**closed_restaurants**
```sql
id, name, address, city, zip, google_place_id, yelp_id,
closure_date, closure_date_estimated, closure_source, created_at
```

**backtest_cohort**
```sql
id, restaurant_id, cohort_type (retrospective/prospective),
baseline_score, baseline_risk_band, baseline_date, baseline_factors JSONB,
outcome_90d, outcome_180d, outcome_90d_date, outcome_180d_date,
closure_date, closure_source, notes, created_at
```

### Score Caps
- Active TABC suspension or expiration: caps overall_score at 40
- Critical health inspection failure (score < 60): caps overall_score at 50
- license_history_risk true: caps overall_score at 65
- sba_default true: caps overall_score at 45
- tax_delinquent 2+ years: caps overall_score at 55
- Composite risk cap (operational < 65 AND velocity < 30): caps overall_score at 59

### Confidence Gates
Minimum 10 data points required before applying scoring adjustments.
Applies to: monthly_volume_trend, ninety_day_slope, recent_vs_lifetime_gap, owner_response_rate.
insufficient_data never triggers a red warning badge in the demo UI.

### Signal Confidence States
- `confirmed` — record found and matched
- `not_applicable` — business type does not require this signal
- `expected_not_found` — record expected based on Google Place types but not found (risk flag)
- `unknown` — cannot determine whether record should exist

TABC expected_not_found: Google Place types include bar, night_club, brewery, winery but no TABC record
Inspection expected_not_found: Food service types in city with working portal but no inspection records

### overall_score Weights
- review_velocity_score: 20%
- rating_trend_score: 30%
- operational_score: 30%
- financial_risk_score: 20%

### Score Risk Bands
- 80-100: Low risk (green)
- 60-79: Moderate risk (yellow)
- 40-59: Elevated risk (orange)
- 0-39: High risk (red)

---

## API Service (.NET)

### Key Endpoints
- GET /health
- GET /api/v1/restaurants — paginated list with latest overall_score
- GET /api/v1/restaurants/{id} — full details with score breakdown
- POST /api/v1/restaurants — register restaurant
- POST /api/v1/restaurants/onboard — name + address, triggers lookup and onboarding
- POST /api/v1/restaurants/search-and-score — name + address + city, cached or fresh score in 30s
- GET /api/v1/backtesting/summary — latest accuracy report
- GET /api/v1/backtesting/cohort — paginated cohort with outcomes
- GET /api/v1/admin/outscraper-quota — current month usage summary with backfill separated
- GET /api/v1/admin/outscraper-runs — last 10 run history entries

### search-and-score Behavior
- Uses findplacefromtext with name+address for single precise match
- Single high-confidence match: skips disambiguation, goes straight to scoring
- Multiple matches: returns disambiguation list
- Scored within 24 hours: return cached score
- Stale score: trigger fresh run
- Not found: trigger full onboarding pipeline
- Timeout: 30 seconds, returns partial result

---

## Scraper Service (Python)

### ⚠️ DATA STORAGE POLICY — NON-NEGOTIABLE

Raw data paid for or fetched from any external source MUST be stored in full in raw_signals.
Aggregations are computed ON TOP of raw data, never INSTEAD of it.

ALWAYS store in raw_signals payload:
- The complete API response exactly as received
- Every field returned regardless of whether currently used
- Individual records (reviews, inspections, loans) as arrays — never discard elements

THEN compute aggregations alongside raw data:
- Monthly breakdowns, averages, trend metrics, keyword findings go in same payload
- Aggregations can always be recomputed — raw data discarded is gone forever

NEVER:
- Store only aggregated output and discard source records
- Filter or truncate API responses before storing
- Decide at scrape time what fields "will be needed"
- Optimize payload size at expense of completeness

Background: In Phase 2 Outscraper scraper incorrectly discarded reviews_data array,
storing only monthly_breakdown. 46,488 paid reviews were lost. Fixed — outscraper_reviews.py
now stores full reviews_data alongside all aggregations.

### Signal Sources
| Signal | Weight | Source | Key Required | Notes |
|---|---|---|---|---|
| Google review velocity | High | Google Places API v1 | Yes | Stores full response + types field |
| Google rating trend | High | Google Places API v1 | Yes | |
| Outscraper reviews | High | Outscraper | Yes | Full reviews_data stored, 70 reviews/restaurant |
| Foursquare rating | Medium | Foursquare | Yes (fsq3...) | Inactive |
| Health inspections | High | See city routing table | No | Confidence classification added |
| TABC license | High | Texas Open Data Portal | No | Confidence classification added |
| Hours consistency | Medium | Google Places snapshots | No | |
| SBA loan history | High | SBA Data.gov API | No | Fuzzy matching min 80 |
| Property tax | High | County CAD portals | No | May return no_data_available |
| Delivery platforms | Medium | DoorDash + Uber Eats | No | platform_unavailable in Docker |
| Job postings | Medium | Placeholder | TBD | |
| Website uptime | Low | Direct HTTP check | No | |

### Health Inspection City Routing
| City | Health Authority | Result |
|---|---|---|
| Dallas | City of Dallas | inspections.myhealthdepartment.com/dallas |
| Fort Worth | Tarrant County | inspections.myhealthdepartment.com/tarrant |
| Arlington | Tarrant County | inspections.myhealthdepartment.com/tarrant |
| Grand Prairie | Tarrant County | inspections.myhealthdepartment.com/tarrant |
| Plano | Collin County | inspections.myhealthdepartment.com/plano |
| Frisco | Collin County | inspections.myhealthdepartment.com/plano |
| McKinney | Collin County | inspections.myhealthdepartment.com/plano |
| Irving | Dallas County | no_inspection_data |
| Garland | Dallas County | no_inspection_data |
| Denton | Denton County | no_inspection_data |

### Property Tax CAD Routing
| City | CAD |
|---|---|
| Dallas, Irving, Garland | Dallas CAD (dallascad.org) |
| Fort Worth, Arlington, Grand Prairie | Tarrant CAD (tad.org) |
| Plano, Frisco, McKinney | Collin CAD (collincad.org) |
| Denton | Denton CAD (dentoncad.com) |

### Outscraper Configuration
- Reviews per restaurant: 70 (full reviews_data array stored)
- Schedule: biweekly — 1st and 15th of month at 1:00 AM UTC
- Monthly projected usage: 107 × 70 × 2 = 14,980 records
- Monthly projected cost: ~$44.94
- Hard cap: 15,000 records/month enforced via outscraper_quota.py
- Safety threshold: 500 records — run skipped if remaining < 500
- Throttle behavior: partial run if remaining < full projected, prioritizing least-recently scraped
- Quota exhausted: log outscraper_quota_exceeded to raw_signals
- Backfill: one-time 200-review pull for restaurants missing May 25+ history (complete)
- Backfill records exempt from monthly cap, tracked separately
- Run history: outscraper_run_log table, status ok/throttled/skipped
- May 2026 usage: 4,975 regular + 27,973 backfill = 32,948 total

### Google Places API Limitation
Returns maximum 5 reviews, not date-sorted. rankPreference: NEWEST not supported on
details endpoint. Reviews sorted internally by _review_dt(). Outscraper is primary source.
Google Places now stores types field for signal confidence classification.

### Review Data Priority
- Sparkline and velocity: Outscraper monthly_breakdown preferred, Google Places fallback
- Recency (days_since_last_review): minimum of Outscraper and Google Places timestamps
- Review count: Outscraper total_reviews_fetched when Google returns 0
- Keyword analysis: Outscraper reviews_data (full text required)
- Rating distribution: Outscraper reviews_data (individual ratings required)
- Response rate: Outscraper owner_answer field

### Keyword Analysis Categories (keyword_analyzer.py)
- operational_instability: closed early, not open, inconsistent hours
- ownership_change: new owner, new management, under new ownership
- sanitation_risk: health code, roaches, cockroach, dirty, unsanitary
- quality_decline: not what it used to be, gone downhill, used to be better
- financial_stress: prices went up, portion smaller, raised prices

### Seasonality Adjustment (seasonality.py)
- January: 0.80, February: 0.90, March: 1.05, April: 1.10, May: 1.10
- June: 1.05, July: 0.95, August: 0.90, September: 1.05
- October: 1.15, November: 1.10, December: 1.15

### Scraper Schedule
- Google Places: daily 2:00 AM UTC
- Hours monitor: daily 3:00 AM UTC
- Scoring engine (engine_v2.py): daily 5:00 AM UTC
- Outscraper reviews: biweekly 1st and 15th at 1:00 AM UTC (~$44.94/month)
- SBA loans: weekly Sunday 1:30 AM UTC
- Property tax: weekly Sunday 2:00 AM UTC
- Outcome tracker: weekly Sunday 3:00 AM UTC
- Delivery platforms: weekly Monday 5:00 AM UTC
- Health inspections + TABC: weekly Monday 4:00-4:30 AM UTC
- New restaurant check: every 10 minutes

### Restaurant Classifier
restaurant_classifier.py verifies businesses before onboarding:
- RESTAURANT: Google Place types include restaurant, food, meal_takeaway, bar, cafe, bakery
- NON_RESTAURANT: liquor_store, convenience_store, grocery_or_supermarket, or name keywords
- All cohort builder and onboarding flows verify classification before inserting

---

## Backtesting Framework

### Current Status (May 2026)
- **Closed restaurant dataset**: 72 verified closed DFW restaurants
- **Retrospective backtest**: 9 restaurants scored at T-90 and T-180
- **Prospective cohort**: 107 verified DFW restaurants (cleaned)
- **Cohort baselined**: June 1, 2026 (re-baselined after full signal set active)
- **90-day outcomes due**: September 1, 2026
- **180-day outcomes due**: December 1, 2026

### Prospective Cohort Distribution
| Band | Count |
|---|---|
| Low (80-100) | 7 |
| Moderate (60-79) | 47 |
| Elevated (40-59) | 51 |
| High (0-39) | 2 |
| **Total** | **107** |

### Model Performance (Retrospective, n=9)
- **Precision**: 100%
- **Recall**: 100% (after composite risk cap)
- **AUC-ROC**: null — requires resolved prospective outcomes
- Sample size preliminary — grows as prospective cohort resolves Sep/Dec 2026

### Composite Risk Cap
Finding: strong rating_trend was masking simultaneous weakness in operational and velocity.
Fix: if operational_score < 65 AND review_velocity_score < 30, cap overall_score at 59.
Recall improved from 77.8% to 100%.

### Customer Pitch Summary
"We backtested against 72 closed DFW restaurants. Our model correctly flagged 100% of
closures before they happened with zero false positives. We have 107 verified DFW
restaurants in our prospective cohort — 90-day outcome data arrives September 2026."

### Demo Restaurants
- **Healthy example**: Pecan Lodge, 2702 Main St, Dallas — score 93, all green
- **Distressed example**: Backyard Dallas — score 51, TABC_MISSING, INSP_MISSING,
  89 days since last review, sharply declining volume, 0% owner response, 3/7 days open
- **Backup distressed**: The Free Man Cajun Cafe, 2630 Commerce St, Dallas

---

## Demo UI (http://localhost:3000)

- Served via nginx — must use http://localhost:3000, NOT file://
- Calls .NET API at http://localhost:8080
- Search: Restaurant Name (required), Address or Zip (required), City (optional dropdown)

### Four Component Score Bars
- Review Velocity (20%)
- Rating Trend (30%)
- Operational Health (30%)
- Financial Risk (20%)

### Review Trend Visualization
- Dual line year-over-year SVG chart
- Current year: solid blue with filled area
- Prior year: dashed gray
- X axis: Jan-Dec, 3-character abbreviations
- Hover crosshair: both years, YoY%, seasonally adjusted %
- YoY summary line: green/red

### Rating Trend Drill-Down
- Rating distribution stacked bar: last 90 days vs lifetime
- Keyword flags: colored chips (red=sanitation/instability, orange=ownership/quality, yellow=financial)
- Owner engagement row: response rate recent vs prior with trend arrow

### Signal Confidence Display
- confirmed: existing display
- expected_not_found: red indicator "Expected — Not Found" with tooltip
- not_applicable: gray "N/A" with tooltip
- unknown: gray "No Data"

### Warning Badges
- review_gap_alert, one_star_spike, rating_deterioration, source_divergence
- sba_default, repeated_sba_borrowing, tax_delinquent
- delivery_platform_loss
- composite_risk_cap (orange — "Composite Risk Flag")
- sanitation_flag (red — "Sanitation Risk")
- owner_disengaged (orange — "Owner Disengaged")
- bimodal_distribution (orange — "Polarized Reviews")
- ownership_change_flag (yellow — "Ownership Change Detected")
- tabc_expected_missing (red — "License Missing")
- inspection_expected_missing (orange — "Inspection Records Missing")

### Features
- Recently scored list — clickable, auto-populates, active highlight
- Side-by-side comparison with difference highlighting
- Disambiguation list for multiple matches
- Plain English risk recommendation per risk band
- Backtesting tab with accuracy summary and cohort status
- Admin footer: Outscraper usage meter, run history dots (green/yellow/red), projected cost

---

## Environment Variables (.env)

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=bizhealth
POSTGRES_USER=admin
POSTGRES_PASSWORD=

GOOGLE_PLACES_API_KEY=
FOURSQUARE_API_KEY=        # inactive
OUTSCRAPER_API_KEY=
TRIPADVISOR_API_KEY=       # not yet built
ANTHROPIC_API_KEY=

ASPNETCORE_ENVIRONMENT=Development
```

---

## Developer Profile
- Experienced C# / .NET developer — standard .NET patterns, no over-explaining
- New to Python — add inline comments on patterns that differ from C#
- Docker Desktop on Windows host
- 107 verified DFW restaurants in prospective cohort
- 86 restaurants with Outscraper data (8 missing = closed retrospective restaurants, expected)
- Pecan Lodge google_place_id: ChIJGXYxd92YToYR7yV_BSMQ2Xk (verified)

---

## Current Focus
> Update this section as the project progresses.

**Phase 1 — COMPLETE** — Scaffolding, Google Places scraper, raw_signals confirmed

**Phase 2 — COMPLETE** — Foursquare (replaced Yelp), scoring engine, health_scores

**Phase 3 — COMPLETE** — Health inspections, TABC, hours monitor, operational_score

**Phase 3b — COMPLETE** — Trend analysis, health_scores extended, EF Core migration

**Phase 4 — COMPLETE** — APScheduler, all jobs automated

**Phase 5 — COMPLETE** — Bulk CSV onboarding, dynamic scheduler, new endpoints

**Phase 6 — COMPLETE** — Demo UI, three-level drill-down, search-and-score

**Phase 6b — COMPLETE** — Seasonality adjustment, enhanced score factors

**Phase 6c — COMPLETE** — Confidence gates, insufficient_data handling

**Phase 7 — COMPLETE** — Outscraper, all 10 DFW cities, Google Places v1

**Phase 8 — COMPLETE** — SBA loans, property tax, financial_risk_score

**Phase 9 — COMPLETE** — DoorDash/Uber Eats checker, infrastructure ready

**Phase 10 — COMPLETE** — Full backtesting framework, 100% precision/recall,
composite risk cap, restaurant classifier, 107 verified cohort

**Phase 11 — COMPLETE** — Demo UI polish, dual line YoY chart, clickable recently scored,
Outscraper biweekly 70 reviews, 15,000 cap, rating trend enhanced (distribution,
keywords, response rate), full reviews_data stored, data storage policy added,
signal confidence classification (TABC + inspections), outscraper_run_log table,
safety threshold 500 records, Backyard Dallas confirmed TABC_MISSING + INSP_MISSING,
customer validation PDF generated (Tableside_API_CustomerValidation.pdf)

**Phase 12 — Customer Validation (current)**
- LinkedIn outreach messages drafted for distributors, equipment lenders, landlords
- 20-minute demo script prepared with Pecan Lodge (93) and Backyard Dallas (51)
- Customer validation PDF generated and ready
- Goal: first customer conversation by end of June 2026
- 90-day prospective cohort outcomes due September 1, 2026
- Next action: send first 3 LinkedIn outreach messages this week

**Phase 13 — COMPLETE** — Out-of-time holdout validation (2022 cutoff), lead-time analysis,
score-band outcome table, methodology_notes.md, 3 new backtesting API endpoints + demo UI sections.
Honest test-set recall: 0% without composite cap (data gap — review signals still decent at closure).

**Phase 14 — COMPLETE** — businessStatus + hours-per-day real signals added
- google_places.py: businessStatus added to field mask, stored in result payload
- hours_monitor.py: total_weekly_hours, avg_hours_per_day, hours_reduction_pct computed from periods
- engine_v2.py: TEMPORARILY_CLOSED → -30 operational; PERMANENTLY_CLOSED → cap at 20;
  hours_reduction_pct >= 30% → -15 operational (all domain knowledge, no leakage)
- health_scores: 6 new columns (business_status, temporarily_closed, permanently_closed,
  total_weekly_hours, hours_reduction_pct, hours_reduction)
- Demo UI: Temporarily Closed (red) and Hours Reduced (orange) warning badges
- Holdout audit: both Phase 14 rules PASS (domain knowledge, not derived from test-set data)
- Impact on test-set recall: none yet — signals require live scraper data, not retroactively
  measurable for 2022–2023 retrospective restaurants; will appear in prospective cohort (Sep 2026)

### Score Caps (updated)
- TEMPORARILY_CLOSED (Google): -30 to operational_score
- PERMANENTLY_CLOSED (Google): caps overall_score at 20
- hours_reduction_pct >= 30%: -15 to operational_score
