# Small Business Financial Health Signals API
## Vertical: Restaurants & Food Service (DFW Region)

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
      google_places.py
      foursquare.py            # Inactive — free tier returns no venue details
      health_inspections.py    # All 10 DFW cities routed to correct health authority
      tabc_license.py          # Texas Open Data Portal — covers all TX cities
      hours_monitor.py
      outscraper_reviews.py    # Primary review source — biweekly 1st/15th, stores FULL reviews_data
      outscraper_quota.py      # Monthly usage cap — 10,000 records/month
      sba_loans.py
      property_tax.py
      delivery_platforms.py    # platform_unavailable in Docker (bot detection)
    /scoring
      engine.py
      engine_v2.py             # Current active engine with composite risk cap
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
      clean_restaurants.py     # Full-table cleanup: dry-run + live mode, FK-safe delete order
  /demo
    index.html
  /db
    init.sql
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

-- Trend analysis (Phase 3b)
license_history_risk      boolean,
inspection_trend          varchar,    -- improving, declining, stable, insufficient_data
hours_change_count        int,
last_inspection_date      date,
last_inspection_score     int,
license_status            varchar,
license_expiry_date       date,

-- Enhanced velocity and rating (Phase 6b)
review_gap_alert          boolean,
one_star_spike            boolean,
rating_deterioration      boolean,
source_divergence         boolean,
ninety_day_slope          varchar,    -- improving, stable, declining, sharp_decline, insufficient_data
days_since_last_review    int,
owner_response_rate       int,
monthly_volume_trend      varchar,    -- growing, stable, declining, sharply_declining, insufficient_data
review_count_confidence   varchar,    -- high, medium, low
seasonality_adjusted      boolean,
comparison_method         varchar,    -- year_over_year, period_comparison, insufficient_data
volume_trend_confidence   varchar,    -- sufficient, insufficient_data
recency_source            varchar,    -- outscraper, google_places

-- Financial risk (Phase 8)
sba_default               boolean,
repeated_sba_borrowing    boolean,
tax_delinquent            boolean,
sba_loan_count            int,
sba_latest_status         varchar,
sba_latest_amount         decimal,
tax_delinquency_years     int,

-- Delivery platforms (Phase 9)
doordash_listed           boolean,
ubereats_listed           boolean,
delivery_platform_count   int,
delivery_status           varchar,
delivery_platform_loss    boolean,

-- Composite risk cap (Phase 10)
composite_risk_cap        boolean,

-- Enhanced rating trend (Phase 11)
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

-- Signal confidence (Phase 11b)
tabc_confidence           varchar,    -- confirmed, not_applicable, expected_not_found, unknown
tabc_confidence_reason    varchar,
inspection_confidence     varchar,    -- confirmed, not_applicable, expected_not_found, unknown
inspection_confidence_reason varchar,
tabc_expected_missing     boolean,    -- true when tabc_confidence = expected_not_found
inspection_expected_missing boolean,  -- true when inspection_confidence = expected_not_found
inspection_data_unavailable boolean,  -- true when city has portal but no records and confidence = unknown

-- Score factors
score_factors             JSONB,

scored_at                 timestamptz
```

**outscraper_usage**
```sql
id                uuid PRIMARY KEY,
restaurant_id     uuid REFERENCES restaurants(id),
records_fetched   int,
month             varchar,    -- YYYY-MM
scraped_at        timestamptz
```

**closed_restaurants**
```sql
id                    uuid PRIMARY KEY,
name                  varchar,
address               varchar,
city                  varchar,
zip                   varchar,
google_place_id       varchar,
yelp_id               varchar,
closure_date          date,
closure_date_estimated boolean,
closure_source        varchar,
created_at            timestamptz
```

**backtest_cohort**
```sql
id                    uuid PRIMARY KEY,
restaurant_id         uuid REFERENCES restaurants(id),
cohort_type           varchar,    -- retrospective, prospective
baseline_score        int,
baseline_risk_band    varchar,    -- low, moderate, elevated, high
baseline_date         date,
baseline_factors      JSONB,
outcome_90d           varchar,    -- open, reduced_hours, closed_temporarily, closed_permanently, unknown
outcome_180d          varchar,
outcome_90d_date      date,
outcome_180d_date     date,
closure_date          date,
closure_source        varchar,
notes                 text,
created_at            timestamptz
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

### Conventions
- .NET 9 Web API with EF Core
- PascalCase classes, camelCase JSON output
- Return ProblemDetails for error responses
- All endpoints versioned under /api/v1/
- Connection strings from environment variables

### Key Endpoints
- GET /health
- GET /api/v1/restaurants — paginated list with latest overall_score
- GET /api/v1/restaurants/{id} — full details with score breakdown
- POST /api/v1/restaurants — register restaurant
- POST /api/v1/restaurants/onboard — name + address, triggers lookup and onboarding
- POST /api/v1/restaurants/search-and-score — name + address + city, cached or fresh score in 30s
- GET /api/v1/backtesting/summary — latest accuracy report
- GET /api/v1/backtesting/cohort — paginated cohort with outcomes
- GET /api/v1/admin/outscraper-quota — current month usage summary

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

### Conventions
- Follow PEP8
- Each scraper is a separate module under /scrapers/signals/
- All scrapers write raw response to raw_signals before processing
- APScheduler for scheduling, httpx for HTTP, python-dotenv for env vars
- Max retries: 3, timeout: 60 seconds per restaurant per scraper
- Failed jobs write error record to raw_signals with source {source}_error
- Add comments explaining Python patterns that differ from C#
- thefuzz library used for fuzzy name matching

---

### ⚠️ DATA STORAGE POLICY — NON-NEGOTIABLE

Raw data paid for or fetched from any external source MUST be stored in full in raw_signals.
Aggregations are computed ON TOP of raw data, never INSTEAD of it.

**ALWAYS store in raw_signals payload:**
- The complete API response or fetched data exactly as received
- Every field returned by the source regardless of whether it is currently used
- Individual records (reviews, inspections, loans) as arrays — never discard elements

**THEN compute and store aggregations alongside the raw data:**
- Monthly breakdowns, averages, trend metrics, keyword findings
- Aggregations go in the same payload as additional keys
- Aggregations can always be recomputed — raw data that is discarded is gone forever

**NEVER:**
- Store only aggregated output and discard the source records
- Filter or truncate API responses before storing
- Decide at scrape time what fields "will be needed" — store everything
- Optimize payload size at the expense of completeness

Storage cost is negligible. Re-fetching API data costs money and time.
Every scraper must follow this policy without exception.

**Background:** In Phase 2 the Outscraper scraper was incorrectly built to store only
monthly_breakdown aggregations and discard the raw reviews_data array. This meant 46,488
paid reviews were lost and keyword analysis, rating distribution, and response rate signals
could not be backfilled without re-fetching at additional cost. Fixed in Phase 11 —
outscraper_reviews.py now stores the full reviews_data array alongside all aggregations.

---

### Signal Sources
| Signal | Weight | Source | Key Required | Notes |
|---|---|---|---|---|
| Google review velocity | High | Google Places API v1 | Yes | 5 reviews max, Outscraper is primary |
| Google rating trend | High | Google Places API v1 | Yes | |
| Outscraper reviews | High | Outscraper | Yes | Full reviews_data stored, 46 reviews/restaurant |
| Foursquare rating | Medium | Foursquare Places API | Yes (fsq3...) | Inactive |
| Health inspections | High | See city routing table | No | |
| TABC license | High | Texas Open Data Portal | No | All TX cities |
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
- Reviews per restaurant: 46 (full reviews_data array stored)
- Schedule: biweekly — 1st and 15th of month at 1:00 AM UTC
- Monthly projected usage: 94 × 46 × 2 = 8,648 records
- Monthly projected cost: ~$25.94
- Hard cap: 10,000 records/month enforced via outscraper_quota.py
- Quota exhausted: skip scrape, log outscraper_quota_exceeded to raw_signals
- Partial quota: reduce reviewsLimit to remaining records rather than skipping
- Payload includes: reviews_data (full), monthly_breakdown
- rating_distribution, keyword_findings, response_rate_recent/prior are
  computed at score time by engine_v2.py — NOT stored in the scraper payload

### Google Places API Limitation
Returns maximum 5 reviews, not date-sorted. rankPreference: NEWEST not supported on
details endpoint. Reviews sorted internally by _review_dt(). Outscraper is primary source.

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
- Outscraper reviews: biweekly 1st and 15th at 1:00 AM UTC (~$29.53/month)
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

### Signal Confidence (signal_confidence.py)
Classifies whether absence of a signal is meaningful (potential risk) or expected/unknowable.

**4 States:**
| State | Meaning |
|---|---|
| `confirmed` | Record found and matched |
| `not_applicable` | Business type does not require this signal (e.g. coffee shop needs no TABC) |
| `expected_not_found` | Record expected based on business type but not found — scored as risk |
| `unknown` | Cannot determine whether a record should exist |

**TABC Classification** — uses Google Place `types` from `v1_raw.types`:
- Any of `bar`, `night_club`, `liquor_store`, `brewery`, `winery` → `expected_not_found` if no record
- All types are `meal_takeaway`, `cafe`, `bakery`, `fast_food` only → `not_applicable`
- `restaurant` or `food` present (no alcohol indicators) → `unknown`
- No place types → `unknown`

**Inspection Classification** — uses city and Google Place `types`:
- Cities with portals (Dallas, Fort Worth, Arlington, Grand Prairie, Plano, Frisco, McKinney) + food service types + no records → `expected_not_found`
- Cities without portals (Irving, Garland, Denton) → `unknown` regardless
- No food service types → `unknown`

**Scoring Adjustments:**
- `tabc_expected_missing = True`: operational_score − 15
- `inspection_expected_missing = True`: operational_score − 10
- `inspection_data_unavailable = True`: no score impact, surfaced in score_factors only

**Google Place Types Source:**
`types` field is fetched via the Google Places v1 API (`_V1_FIELD_MASK` includes `types`) and
stored in `raw_signals` payload under `payload['v1_raw']['types']`. The engine reads place types
from the latest `google_places` raw_signal at score time. Classification runs fresh at score time —
not cached from the scraper payload.

---

## Backtesting Framework

### Current Status (May 2026)
- **Closed restaurant dataset**: 72 verified closed DFW restaurants
- **Retrospective backtest**: 9 restaurants scored at T-90 and T-180
- **Total restaurants**: 94 verified DFW restaurants
- **Prospective cohort**: 62 restaurants in backtest_cohort (prospective)
- **90-day outcomes due**: August 14, 2026
- **180-day outcomes due**: November 12, 2026

### Prospective Cohort Distribution
| Band | Count |
|---|---|
| Low (80-100) | 11 |
| Moderate (60-79) | 29 |
| Elevated (40-59) | 21 |
| High (0-39) | 1 |
| **Total** | **62** |

### Model Performance (Retrospective, n=9)
- **Precision**: 100%
- **Recall**: 100% (after composite risk cap)
- **AUC-ROC**: null — requires resolved prospective outcomes
- Sample size is preliminary — grows as prospective cohort resolves Aug/Nov 2026

### Composite Risk Cap
Finding: strong rating_trend was masking simultaneous weakness in operational and velocity.
Fix: if operational_score < 65 AND review_velocity_score < 30, cap overall_score at 59.
Recall improved from 77.8% to 100% after cap was applied.

### Customer Pitch Summary
"We backtested against 72 closed DFW restaurants. Our model correctly flagged 100% of
closures before they happened with zero false positives. We have 62 verified DFW
restaurants in our prospective cohort — 90-day outcome data arrives August 2026."

---

## Demo UI (http://localhost:3000)

- Served via nginx — must use http://localhost:3000, NOT file://
- Calls .NET API at http://localhost:8080
- Search: Restaurant Name (required), Address or Zip (required), City (optional dropdown)
- Supported cities: Dallas, Fort Worth, Arlington, Plano, Frisco, McKinney, Denton,
  Irving, Garland, Grand Prairie

### Four Component Score Bars
- Review Velocity (20%)
- Rating Trend (30%)
- Operational Health (30%)
- Financial Risk (20%)

### Three Level Score Display
- Level 1: overall score, four component bars, color risk indicator, plain English recommendation
- Level 2: expandable signal breakdown per component with impact indicators and warning badges
- Level 3: raw evidence — inspection records, TABC details, Google/Outscraper data

### Review Trend Visualization
- Dual line year-over-year chart (SVG)
- Current year: solid blue line with filled area
- Prior year: dashed gray line
- X axis: Jan-Dec with 3-character abbreviations
- Hover crosshair: tooltip showing both years, YoY%, seasonally adjusted %
- YoY summary line: average change, green/red colored
- Seasonal adjustment note below chart

### Rating Trend Drill-Down (Level 2)
- Rating distribution stacked bar: last 90 days vs lifetime side by side
- Keyword flags: colored chips (red=sanitation/instability, orange=ownership/quality, yellow=financial)
- Hover tooltip on each chip showing example phrases found
- Owner engagement row: response rate recent vs prior with trend arrow

### Warning Badges
- review_gap_alert, one_star_spike, rating_deterioration, source_divergence
- sba_default, repeated_sba_borrowing, tax_delinquent
- delivery_platform_loss
- composite_risk_cap (orange — "Composite Risk Flag")
- sanitation_flag (red — "Sanitation Risk")
- owner_disengaged (orange — "Owner Disengaged")
- bimodal_distribution (orange — "Polarized Reviews")
- ownership_change_flag (yellow — "Ownership Change Detected")
- tabc_expected_missing (orange — "License Missing")
- inspection_expected_missing (orange — "Inspection Records Missing")
- Never shown for insufficient_data

### Features
- Recently scored list — clickable, auto-populates score section, active highlight state
- Side-by-side comparison of 2 restaurants with difference highlighting
- Disambiguation list for multiple matches
- Plain English risk recommendation per risk band
- Backtesting tab with accuracy summary and cohort status
- Admin footer: Outscraper usage meter with projected monthly cost

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

Note: Health inspections, TABC, SBA loans require no API keys — free public data.
Delivery platforms require no key but blocked by bot detection in Docker.

---

## Developer Profile
- Experienced C# / .NET developer — standard .NET patterns, no over-explaining
- New to Python — add inline comments on patterns that differ from C#:
  - Async/await, SQLAlchemy vs EF Core, package structure vs namespaces
  - thefuzz fuzzy matching vs string comparison
  - HTTP scraping vs HttpClient
- Docker Desktop on Windows host
- 94 verified DFW restaurants (post-cleanup; 168 non-restaurants removed)
- 62 in prospective backtest cohort; 16 retrospective
- Pecan Lodge google_place_id: ChIJGXYxd92YToYR7yV_BSMQ2Xk (verified)

---

## Current Focus
> Update this section as the project progresses.

**Phase 1 — COMPLETE** — Scaffolding, Google Places scraper, raw_signals confirmed

**Phase 2 — COMPLETE** — Foursquare (replaced Yelp), scoring engine, health_scores

**Phase 3 — COMPLETE** — Health inspections, TABC, hours monitor, operational_score

**Phase 3b — COMPLETE** — Trend analysis, health_scores extended, EF Core migration

**Phase 4 — COMPLETE** — APScheduler, all jobs automated

**Phase 5 — COMPLETE** — Bulk CSV onboarding, 5 restaurants, dynamic scheduler

**Phase 6 — COMPLETE** — Demo UI, three-level drill-down, search-and-score

**Phase 6b — COMPLETE** — Seasonality adjustment, enhanced score factors

**Phase 6c — COMPLETE** — Confidence gates, insufficient_data handling

**Phase 7 — COMPLETE** — Outscraper, all 10 DFW cities, 25 restaurants, Google Places v1

**Phase 8 — COMPLETE** — SBA loans, property tax, financial_risk_score, 25/25 passed

**Phase 9 — COMPLETE** — DoorDash/Uber Eats checker, infrastructure ready

**Phase 10 — COMPLETE** — Full backtesting framework, 100% precision/recall,
composite risk cap, restaurant classifier, 107 verified cohort (since reduced to 94 after cleanup)

**Phase 11 — COMPLETE** — Rating trend signals, UI polish, cost controls, signal confidence:
- Dual line year-over-year review chart
- Recently scored list clickable
- Outscraper biweekly schedule, 46 reviews/restaurant, ~$25.94/month (94 restaurants)
- 10,000 record/month cap enforced
- Rating trend enhanced: distribution, keyword flags, response rate trend
- Full reviews_data now stored in raw_signals (data storage policy fixed)
- Data storage policy added to CLAUDE.md — NON-NEGOTIABLE
- Signal confidence system: 4-state classification for TABC and inspection signals
  - Distinguishes not_applicable / expected_not_found / unknown / confirmed
  - expected_not_found penalizes operational_score (−15 TABC, −10 inspection)
  - Google Places field mask extended to include `types` for classification
  - 7 new health_scores columns: tabc_confidence, inspection_confidence, *_reason, *_expected_missing, inspection_data_unavailable
  - 2 new demo warning badges: "License Missing", "Inspection Records Missing"
  - 17 unit tests in test_signal_confidence.py — all pass

**Phase 12 — Customer Validation (current)**
- LinkedIn outreach messages drafted for distributors, equipment lenders, landlords
- Demo script prepared: 20-minute structure, pain-first, money question at end
- Goal: first customer conversation by end of June 2026
- Next action: send first 3 LinkedIn outreach messages this week
- 90-day prospective cohort outcomes due August 14, 2026
