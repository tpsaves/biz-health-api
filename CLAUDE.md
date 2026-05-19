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
      outscraper_reviews.py    # Primary review recency source — biweekly 1st/15th
      outscraper_quota.py      # Monthly usage cap enforcement — 10,000 records/month
      sba_loans.py
      property_tax.py
      delivery_platforms.py    # platform_unavailable in Docker (bot detection)
    /scoring
      engine.py
      engine_v2.py             # Current active engine with composite risk cap
      seasonality.py
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

### Signal Sources
| Signal | Weight | Source | Key Required | Notes |
|---|---|---|---|---|
| Google review velocity | High | Google Places API v1 | Yes | 5 reviews max, Outscraper is primary |
| Google rating trend | High | Google Places API v1 | Yes | |
| Outscraper reviews | High | Outscraper | Yes | Primary source, 46 reviews/restaurant |
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
- Reviews per restaurant: 46
- Schedule: biweekly — 1st and 15th of month at 1:00 AM UTC
- Monthly projected usage: 107 × 46 × 2 = 9,844 records
- Monthly projected cost: ~$29.53
- Hard cap: 10,000 records/month enforced via outscraper_quota.py
- Quota exhausted behavior: skip scrape, log outscraper_quota_exceeded to raw_signals
- Partial quota: reduce reviewsLimit to remaining records rather than skipping

### Google Places API Limitation
Returns maximum 5 reviews, not date-sorted. rankPreference: NEWEST not supported on
details endpoint. Reviews sorted internally by _review_dt(). Outscraper is primary source.

### Review Data Priority
**Sparkline and velocity**: Outscraper monthly_breakdown preferred, Google Places fallback
**Recency (days_since_last_review)**: minimum of Outscraper and Google Places timestamps
**Review count**: Outscraper total_reviews_fetched when Google returns 0

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

---

## Backtesting Framework

### Current Status (May 2026)
- **Closed restaurant dataset**: 72 verified closed DFW restaurants
- **Retrospective backtest**: 9 restaurants scored at T-90 and T-180
- **Prospective cohort**: 107 verified DFW restaurants (148 non-restaurants removed)
- **90-day outcomes due**: August 14, 2026
- **180-day outcomes due**: November 12, 2026

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
- Sample size is preliminary — grows as prospective cohort resolves in Aug/Nov 2026

### Composite Risk Cap
Finding: strong rating_trend was masking simultaneous weakness in operational and velocity.
Fix: if operational_score < 65 AND review_velocity_score < 30, cap overall_score at 59.
Recall improved from 77.8% to 100% after cap was applied.

### Customer Pitch Summary
"We backtested against 72 closed DFW restaurants. Our model correctly flagged 100% of
closures before they happened with zero false positives. We have 107 verified DFW
restaurants in our prospective cohort — 90-day outcome data arrives August 2026."

---

## Demo UI (http://localhost:3000)

- Served via nginx — must use http://localhost:3000, NOT file://
- Calls .NET API at http://localhost:8080
- Search: Restaurant Name (required), Address or Zip (required), City (optional dropdown)

### Features
- Four component score bars: Review Velocity (20%), Rating Trend (30%), Operational (30%), Financial Risk (20%)
- Three level score display: summary, signal breakdown, raw evidence
- Dual line year-over-year review chart (current year solid blue, prior year dashed gray)
- Hover crosshair with tooltip showing both years, YoY%, seasonally adjusted %
- YoY summary line below chart (green/red)
- Recently scored list — clickable, auto-populates score section, active highlight state
- Side-by-side comparison of 2 restaurants with difference highlighting
- Disambiguation list for multiple matches
- Plain English risk recommendation per risk band
- Backtesting tab with accuracy summary and cohort status
- Admin footer: Outscraper usage meter with projected monthly cost

### Warning Badges
- review_gap_alert, one_star_spike, rating_deterioration, source_divergence
- sba_default, repeated_sba_borrowing, tax_delinquent
- delivery_platform_loss
- composite_risk_cap (orange — "Composite Risk Flag")
- Never shown for insufficient_data

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
- 25 restaurants in active scoring pipeline
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

**Phase 10 — COMPLETE** — Full backtesting framework:
- 72 closed restaurants, 107 verified prospective cohort
- 100% precision and recall after composite risk cap
- Restaurant classifier prevents non-restaurant contamination
- Outcome tracker scheduled weekly

**Phase 11 — COMPLETE** — Demo UI polish and cost controls:
- Dual line year-over-year review chart with hover tooltips and YoY summary
- Recently scored list clickable — auto-populates score section
- Outscraper biweekly schedule (1st/15th), 46 reviews/restaurant
- 10,000 record/month cap enforced via outscraper_quota.py (~$29.53/month)
- Outscraper usage meter in demo UI admin footer
- All 205 restaurants re-scored with Outscraper as primary sparkline source

**Phase 12 — Customer Validation (current)**
- Target contacts identified: Sysco/US Foods district managers, equipment lenders, landlords
- LinkedIn outreach messages drafted for all three customer types
- Demo script prepared: 20-minute structure with pain-first opening and money question
- Goal: first customer conversation by end of June 2026
- Next action: send first 3 LinkedIn outreach messages this week
