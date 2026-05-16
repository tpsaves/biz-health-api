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
      google_places.py         # Google Places API v1
      foursquare.py            # Inactive — free tier returns no venue details
      health_inspections.py    # All 10 DFW cities routed to correct health authority
      tabc_license.py          # Texas Open Data Portal — covers all TX cities
      hours_monitor.py
      outscraper_reviews.py    # Primary review recency source — weekly Sunday 1AM UTC
      sba_loans.py             # SBA 7(a) and 504 loan data via Data.gov
      property_tax.py          # County appraisal district business personal property tax
      delivery_platforms.py    # DoorDash + Uber Eats listing status (platform_unavailable in Docker)
    /scoring
      engine.py
      seasonality.py
    /onboarding
      restaurant_lookup.py
      bulk_onboard.py
  /demo
    index.html
  /db
    init.sql
```

### Docker Compose
- All services run via docker-compose
- The `scrapers` service populates data; the `api` service reads it
- Postgres hostname inside Docker network is `db`
- Postgres exposed to Windows host at `localhost:5432` for GUI tools
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
tabc_license, hours_monitor, sba_loans, property_tax, delivery_platforms, {source}_error

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
sba_latest_status         varchar,    -- active, paid_in_full, charged_off, none_found
sba_latest_amount         decimal,
tax_delinquency_years     int,

-- Delivery platforms (Phase 9)
doordash_listed           boolean,
ubereats_listed           boolean,
delivery_platform_count   int,        -- 0, 1, or 2
delivery_status           varchar,    -- active, partial, offline, never_listed, unknown
delivery_platform_loss    boolean,

-- Score factors
score_factors             JSONB,

scored_at                 timestamptz
```

### Score Caps
- Active TABC suspension or expiration: caps overall_score at 40
- Critical health inspection failure (score < 60): caps overall_score at 50
- license_history_risk true: caps overall_score at 65
- sba_default true: caps overall_score at 45
- tax_delinquent 2+ years: caps overall_score at 55

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

### Conventions
- All tables use snake_case
- All timestamps UTC
- Raw payloads stored as JSONB
- Never use localhost in connection strings inside containers

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

### search-and-score Behavior
- Uses findplacefromtext with full name+address string for single precise match
- Single high-confidence match: skips disambiguation, goes straight to scoring
- Multiple matches: returns disambiguation list
- Scored within 24 hours: return cached score
- Stale score: trigger fresh run
- Not found: trigger full onboarding pipeline
- Timeout: 30 seconds, returns partial result

### Score API Response Shape
```json
{
  "overallScore": 88,
  "reviewVelocityScore": 70,
  "ratingTrendScore": 85,
  "operationalScore": 95,
  "financialRiskScore": 100,
  "staffingScore": null,
  "licenseStatus": "active",
  "licenseHistoryRisk": false,
  "licenseExpiryDate": "2027-03-15",
  "inspectionTrend": "improving",
  "lastInspectionDate": "2026-02-10",
  "lastInspectionScore": 100,
  "hoursChangeCount": 0,
  "reviewGapAlert": false,
  "oneStarSpike": false,
  "ratingDeterioration": false,
  "sourceDivergence": false,
  "ninetyDaySlope": "stable",
  "daysSinceLastReview": 15,
  "recencySource": "outscraper",
  "ownerResponseRate": null,
  "monthlyVolumeTrend": "sharply_declining",
  "reviewCountConfidence": "high",
  "seasonalityAdjusted": true,
  "comparisonMethod": "year_over_year",
  "volumeTrendConfidence": "sufficient",
  "sbaDefault": false,
  "repeatedSbaBorrowing": false,
  "taxDelinquent": false,
  "sbaLoanCount": 0,
  "sbaLatestStatus": "none_found",
  "taxDelinquencyYears": 0,
  "doordashListed": null,
  "uberEatsListed": null,
  "deliveryPlatformCount": null,
  "deliveryStatus": "unknown",
  "deliveryPlatformLoss": false,
  "scoreFactors": {
    "operational": [],
    "reviewVelocity": [],
    "ratingTrend": [],
    "financialRisk": []
  }
}
```

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
- thefuzz library used for fuzzy name matching in SBA, property tax, and delivery platform scrapers

### Signal Sources
| Signal | Weight | Source | Key Required | Notes |
|---|---|---|---|---|
| Google review velocity | High | Google Places API v1 | Yes | 5 reviews, not date-sorted — Outscraper is primary recency source |
| Google rating trend | High | Google Places API v1 | Yes | |
| Outscraper reviews | High | Outscraper | Yes | Primary recency source, date-sorted, free tier = 3 reviews/call |
| Foursquare rating | Medium | Foursquare Places API | Yes (fsq3...) | Inactive — free tier returns no venue details |
| Health inspections | High | See city routing table | No | |
| TABC license | High | Texas Open Data Portal | No | Covers all TX cities |
| Hours consistency | Medium | Google Places snapshots | No | |
| SBA loan history | High | SBA Data.gov API | No | Fuzzy name matching, min score 80 |
| Property tax | High | County CAD portals | No | Fuzzy name matching, may return no_data_available |
| Delivery platforms | Medium | DoorDash + Uber Eats public search | No | Returns platform_unavailable from Docker due to bot detection |
| Job postings | Medium | Placeholder — not yet built | TBD | |
| Website uptime | Low | Direct HTTP check | No | |

### Health Inspection City Routing
| City | Health Authority | Portal |
|---|---|---|
| Dallas | City of Dallas | inspections.myhealthdepartment.com/dallas |
| Fort Worth | Tarrant County Public Health | inspections.myhealthdepartment.com/tarrant |
| Arlington | Tarrant County Public Health | inspections.myhealthdepartment.com/tarrant |
| Grand Prairie | Tarrant County Public Health | inspections.myhealthdepartment.com/tarrant |
| Plano | City of Plano / Collin County | inspections.myhealthdepartment.com/plano |
| Frisco | Collin County | inspections.myhealthdepartment.com/plano |
| McKinney | Collin County | inspections.myhealthdepartment.com/plano |
| Irving | Dallas County Health | Dallas County portal — no_inspection_data |
| Garland | Dallas County Health | Dallas County portal — no_inspection_data |
| Denton | Denton County | Denton County portal — no_inspection_data |

### Property Tax CAD Routing
| City | CAD | URL |
|---|---|---|
| Dallas, Irving, Garland | Dallas CAD | dallascad.org |
| Fort Worth, Arlington, Grand Prairie | Tarrant CAD | tad.org |
| Plano, Frisco, McKinney | Collin CAD | collincad.org |
| Denton | Denton CAD | dentoncad.com |

### Google Places API Limitation
Returns maximum 5 reviews per request. rankPreference: NEWEST is NOT supported on the
Place Details endpoint (causes HTTP 400). Reviews sorted internally by _review_dt().
Outscraper is primary recency source. Future fix: upgrade Outscraper paid tier.

### Review Recency Priority
1. Outscraper reviews (date-sorted, primary) — uses most recent review_datetime_utc
2. Google Places v1 (fallback) — uses most recent publishTime after internal sort
Scoring engine takes minimum of both values.

### Delivery Platform Known Issue
DoorDash and Uber Eats block headless HTTP requests from Docker (401/404).
Scraper returns platform_unavailable — correctly excluded from scoring adjustments.
Future fix options: SerpApi Google Shopping, Playwright browser automation.
Infrastructure and DB columns are in place — ready when data source is resolved.

### Seasonality Adjustment
Defined in /scrapers/scoring/seasonality.py:
- January: 0.80, February: 0.90, March: 1.05, April: 1.10, May: 1.10
- June: 1.05, July: 0.95, August: 0.90, September: 1.05
- October: 1.15, November: 1.10, December: 1.15

### Scraper Schedule
- Google Places: daily 2:00 AM UTC
- Foursquare (inactive): daily 2:30 AM UTC
- Hours monitor: daily 3:00 AM UTC
- Scoring engine: daily 5:00 AM UTC
- Outscraper reviews: weekly Sunday 1:00 AM UTC
- SBA loans: weekly Sunday 1:30 AM UTC
- Property tax: weekly Sunday 2:00 AM UTC
- Delivery platforms: weekly Monday 5:00 AM UTC
- Health inspections + TABC: weekly Monday 4:00-4:30 AM UTC
- New restaurant check: every 10 minutes

### Onboarding
- Bulk CSV onboarding (columns: name, address, city, zip)
- Auto-lookup of Google Place ID and Foursquare ID per restaurant
- Idempotent — upserts without duplicating
- Unmatched restaurants written to unmatched.csv

---

## Demo UI (http://localhost:3000)

- Single self-contained HTML/CSS/JS file at /demo/index.html
- Served via nginx container — must use http://localhost:3000, NOT file://
- Calls .NET API at http://localhost:8080
- Search: Restaurant Name (required), Address or Zip (required), City (optional dropdown)
- Supported cities: Dallas, Fort Worth, Arlington, Plano, Frisco, McKinney, Denton, Irving, Garland, Grand Prairie

### Four Component Score Bars
- Review Velocity (20% weight)
- Rating Trend (30% weight)
- Operational Health (30% weight)
- Financial Risk (20% weight)

### Three Level Score Display
- Level 1: overall score, four component bars, color risk indicator, plain English recommendation
- Level 2: expandable signal breakdown per component with impact indicators and warning badges
- Level 3: raw evidence — inspection records, TABC details, Google/Outscraper data, SBA/tax records

### Warning Badges
- Only shown for confirmed negative signals with sufficient data
- Never shown for insufficient_data
- Active flags: review_gap_alert, one_star_spike, rating_deterioration, source_divergence,
  sba_default, repeated_sba_borrowing, tax_delinquent, delivery_platform_loss

### Sparkline
- Shows last 6 months of review volume
- 3-character month abbreviations: Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec
- Zero-review months: 8px gray outlined bar with hover tooltip
- Seasonal adjustment note below sparkline

### Features
- Recently scored list (last 5)
- Side-by-side comparison of 2 restaurants with difference highlighting
- Disambiguation list for multiple matches
- Plain English risk recommendation per risk band

---

## Environment Variables (.env)

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=bizhealth
POSTGRES_USER=admin
POSTGRES_PASSWORD=

GOOGLE_PLACES_API_KEY=
FOURSQUARE_API_KEY=        # must start with fsq3... (inactive)
OUTSCRAPER_API_KEY=
TRIPADVISOR_API_KEY=       # not yet built
ANTHROPIC_API_KEY=

ASPNETCORE_ENVIRONMENT=Development
```

Note: Health inspections, TABC, SBA loans require no API keys — free public data.
Delivery platforms require no API key but are blocked by bot detection in Docker.

---

## Developer Profile
- Experienced C# / .NET developer — use standard .NET patterns without over-explaining
- New to Python — add inline comments on Python-specific patterns, especially:
  - Async/await differences from C#
  - SQLAlchemy session management vs EF Core DbContext
  - Python package/module structure vs .NET namespaces
  - thefuzz fuzzy matching vs string comparison in C#
  - HTTP scraping patterns vs HttpClient in C#
- Docker Desktop running on Windows host
- 25 DFW restaurants onboarded and scoring

---

## Current Focus
> Update this section as the project progresses.

**Phase 1 — COMPLETE**
- Scaffolding, docker-compose, Google Places scraper end-to-end

**Phase 2 — COMPLETE**
- Foursquare scraper (replaced Yelp — cost prohibitive)
- Scoring engine: review_velocity_score, rating_trend_score, overall_score

**Phase 3 — COMPLETE**
- Health inspection scraper (Dallas + Fort Worth)
- TABC liquor license monitor
- Google hours change detector
- operational_score added, score caps added

**Phase 3b — COMPLETE**
- Trend analysis added to scoring engine
- health_scores extended with trend fields
- license_history_risk caps overall_score at 65

**Phase 4 — COMPLETE**
- APScheduler wired with all jobs running automatically
- Scheduler runs as container entry point

**Phase 5 — COMPLETE**
- Bulk CSV onboarding pipeline, idempotent (35/35 green)
- 5 DFW restaurants onboarded initially
- Dynamic scheduler, new API endpoints

**Phase 6 — COMPLETE**
- Demo UI at http://localhost:3000 (nginx container)
- Three level score display with score factors drill-down
- search-and-score endpoint with 24hr caching
- Disambiguation fixed: name+address resolves to single match

**Phase 6b — COMPLETE**
- Seasonality adjustment module
- Enhanced score factors: monthly volume trend, recency gap, owner response rate,
  1-star spike, 90-day slope, recent vs lifetime gap, cross-source divergence

**Phase 6c — COMPLETE**
- Confidence gate: minimum 10 data points before applying trend penalties
- Gray neutral indicator in demo UI for insufficient_data

**Phase 7 — COMPLETE**
- Outscraper integrated for deep review history
- Health inspection routing for all 10 DFW cities
- TABC confirmed for all DFW cities
- 25 restaurants onboarded and scoring
- Google Places v1 migration
- days_since_last_review: Outscraper primary, Google Places fallback

**Phase 8 — COMPLETE**
- SBA loan history scraper (Data.gov, free, fuzzy matching)
- Business personal property tax scraper (county CADs, fuzzy matching)
- financial_risk_score component added (0-100)
- Score caps: sba_default caps at 45, tax_delinquent 2yr caps at 55
- overall_score weights: velocity 20%, rating 30%, operational 30%, financial 20%
- 25/25 restaurants passed
- Pecan Lodge: overall=88, financial_risk=100, operational=95

**Phase 9 — COMPLETE**
- DoorDash and Uber Eats listing status checker built
- Change detection logic for delivery_platform_loss flag
- delivery_platform_loss warning badge in demo UI
- 25/25 passed — platform_unavailable from Docker (bot detection blocks headless requests)
- Infrastructure and DB columns in place — ready when data source resolved
- Known fix options: SerpApi Google Shopping, Playwright browser automation

**Phase 10 — Options (choose one or more)**
A: API hardening — authentication, rate limiting, ready for first paying customer
B: Delivery platform fix — SerpApi or Playwright to bypass bot detection
C: Outscraper paid tier upgrade — same-week review freshness
D: Customer validation — demo to Sysco/distributor contact, gather feedback
E: VPS deployment — Railway or DigitalOcean for always-on production hosting
