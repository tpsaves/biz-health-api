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
| `demo` | nginx:alpine | Serves the single-page demo UI at http://localhost:3000 |

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
      foursquare.py
      health_inspections.py
      tabc_license.py
      hours_monitor.py
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
- Demo UI served by nginx on port 3000 — no separate Python server needed
- All containers use restart: unless-stopped

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

**health_scores**
```sql
-- Score components
review_velocity_score     int,
rating_trend_score        int,
operational_score         int,
staffing_score            int,        -- null, placeholder for future job posting signal
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
owner_response_rate       int,        -- percentage 0-100, requires 10+ reviews
monthly_volume_trend      varchar,    -- growing, stable, declining, sharply_declining, insufficient_data
review_count_confidence   varchar,    -- high, medium, low
seasonality_adjusted      boolean,
comparison_method         varchar,    -- year_over_year, period_comparison, insufficient_data

-- Confidence gates (Phase 6c)
volume_trend_confidence   varchar,    -- sufficient, insufficient_data

-- Score factors (Phase 6)
score_factors             JSONB,

scored_at                 timestamptz
```

### Score Caps
- Active TABC suspension or expiration: caps overall_score at 40
- Critical health inspection failure (score < 60): caps overall_score at 50
- license_history_risk true: caps overall_score at 65

### Confidence Gates
Minimum 10 data points required before applying scoring adjustments.
Applies to: monthly_volume_trend, ninety_day_slope, recent_vs_lifetime_gap, owner_response_rate.
Fewer than 10 points sets field to insufficient_data with zero penalty or bonus.
insufficient_data never triggers a red warning badge in the demo UI.

### overall_score Weights
- review_velocity_score: 25%
- rating_trend_score: 35%
- operational_score: 40%

### Score Risk Bands
- 80-100: Low risk (green)
- 60-79: Moderate risk (yellow)
- 40-59: Elevated risk (orange)
- 0-39: High risk (red)

### Conventions
- All tables use snake_case
- All timestamps UTC
- Raw payloads stored as JSONB
- Never use localhost in connection strings inside containers — always use service name db

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
- Scored within 24 hours: return cached score
- Stale score: trigger fresh run
- Not found: trigger full onboarding pipeline
- Timeout: 30 seconds, returns partial result
- Multiple matches: return disambiguation list

### Score API Response Shape
```json
{
  "overallScore": 93,
  "reviewVelocityScore": 100,
  "ratingTrendScore": 85,
  "operationalScore": 95,
  "staffingScore": null,
  "licenseStatus": "active",
  "licenseHistoryRisk": false,
  "licenseExpiryDate": "2027-03-15",
  "inspectionTrend": "stable",
  "lastInspectionDate": "2026-02-10",
  "lastInspectionScore": 94,
  "hoursChangeCount": 0,
  "reviewGapAlert": false,
  "oneStarSpike": false,
  "ratingDeterioration": false,
  "sourceDivergence": false,
  "ninetyDaySlope": "insufficient_data",
  "daysSinceLastReview": 4,
  "ownerResponseRate": null,
  "monthlyVolumeTrend": "insufficient_data",
  "reviewCountConfidence": "high",
  "seasonalityAdjusted": true,
  "comparisonMethod": "insufficient_data",
  "volumeTrendConfidence": "insufficient_data",
  "scoreFactors": {
    "operational": [],
    "reviewVelocity": [],
    "ratingTrend": []
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

### Signal Sources
| Signal | Weight | Source | Key Required |
|---|---|---|---|
| Google review velocity | High | Google Places API | Yes |
| Google rating trend | High | Google Places API | Yes |
| Foursquare rating | Medium | Foursquare Places API | Yes (fsq3...) |
| Health inspections | High | City/county portals for all 10 DFW cities | No |
| TABC license | High | Texas Open Data Portal | No |
| Hours consistency | Medium | Google Places snapshots | No |
| Outscraper review history | High | Outscraper Google Maps Reviews API | Yes (OUTSCRAPER_API_KEY) |
| Job postings | Medium | Placeholder — not yet built | TBD |
| Website uptime | Low | Direct HTTP check | No |

### Google Places API
- **Endpoint**: Places API v1 (`https://places.googleapis.com/v1/places/{place_id}`)
- Auth: `X-Goog-Api-Key` header (not query param `key=`)
- Fields: `X-Goog-FieldMask` header — `id,displayName,rating,userRatingCount,currentOpeningHours,regularOpeningHours,reviews`
- Returns maximum 5 reviews per request in Google's default (relevance) order
- `reviews[].publishTime` is ISO 8601 with nanosecond precision (e.g. `2026-02-25T18:47:51.391323873Z`)
- `_review_dt()` in `google_places.py` handles both `publishTime` (v1) and `time` (Unix int, legacy) for backward compatibility with existing raw_signals
- `_extract_velocity_metrics()` sorts reviews by `_review_dt()` internally — `days_since_last_review` is accurate regardless of API return order
- `api_version: "v1"` stored in every raw_signals payload going forward
- Note: `reviews.rankPreference=NEWEST` is NOT supported on Place Details — only on Text/Nearby Search POST body. Attempting it returns HTTP 400.
- True monthly volume history cannot be reconstructed from API alone
- Outscraper (Phase 7) solves this: fetches up to 520 reviews with timestamps for real monthly breakdowns
- Year-over-year comparison uses Outscraper data when available; falls back to period comparison with DFW seasonal normalization
- Confidence gate: minimum 10 data points required before applying trend penalties

### Seasonality Adjustment
Defined in /scrapers/scoring/seasonality.py using DFW industry seasonal factors:
- January: 0.80, February: 0.90, March: 1.05, April: 1.10, May: 1.10
- June: 1.05, July: 0.95, August: 0.90, September: 1.05
- October: 1.15, November: 1.10, December: 1.15
- Year-over-year used when 12 months available, period comparison with normalization as fallback

### Scraper Schedule
- Google Places + Foursquare: daily 2:00-2:30 AM UTC
- Hours monitor: daily 3:00 AM UTC
- Health inspections + TABC: weekly Monday 4:00-4:30 AM UTC
- Outscraper review history: weekly Sunday 1:00 AM UTC (before Sunday scoring run; pay-per-use)
- Scoring engine: daily 5:00 AM UTC
- New restaurant check: every 10 minutes

### Review History Strategy
- Outscraper (weekly) fetches up to 520 reviews with timestamps → real monthly_breakdown in raw_signals
- Scoring engine prefers Outscraper monthly_breakdown for year_over_year comparison
- Fallback: daily Google Places snapshots accumulate over time for period_comparison
- Seasonal normalization applied to all period comparisons

### Onboarding
- Bulk CSV onboarding (columns: name, address, city, zip)
- Auto-lookup of Google Place ID and Foursquare ID per restaurant
- Idempotent — upserts without duplicating
- Unmatched restaurants written to unmatched.csv

---

## Demo UI (/demo/index.html)

- Single self-contained HTML/CSS/JS file, no build tools
- Served by nginx:alpine at http://localhost:3000 (docker-compose demo service)
- Calls .NET API at http://localhost:8080 — resolved by the browser on the host, not from inside the container
- Search: Restaurant Name (required), Address or Zip (required), City (optional dropdown)
- Supported cities: Dallas, Fort Worth, Arlington, Plano, Frisco, McKinney, Denton, Irving, Garland, Grand Prairie
- Health inspection coverage: all 10 DFW cities (routing per city to correct health authority)

### Three Level Score Display
- Level 1: overall score, component bars, color risk indicator, plain English recommendation
- Level 2: expandable signal breakdown per component with impact indicators
- Level 3: raw evidence — inspection records, TABC details, Google/Foursquare data

### Warning Badges
- Only shown for confirmed negative signals with sufficient data
- Never shown for insufficient_data
- Gray neutral indicator with tooltip for insufficient_data fields

### Features
- Recently scored list (last 5)
- Side-by-side comparison of 2 restaurants with difference highlighting
- Disambiguation list for multiple matches
- Monthly review sparkline (12 months where available)
- Seasonal adjustment note below sparkline

---

## Environment Variables (.env)

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=bizhealth
POSTGRES_USER=admin
POSTGRES_PASSWORD=

GOOGLE_PLACES_API_KEY=
FOURSQUARE_API_KEY=        # must start with fsq3...
OUTSCRAPER_API_KEY=
ANTHROPIC_API_KEY=

ASPNETCORE_ENVIRONMENT=Development
```

Note: Health inspection and TABC scrapers require no API keys — both use free public data portals.

---

## Developer Profile
- Experienced C# / .NET developer — use standard .NET patterns without over-explaining
- New to Python — add inline comments on Python-specific patterns, especially:
  - Async/await differences from C#
  - SQLAlchemy session management vs EF Core DbContext
  - Python package/module structure vs .NET namespaces
- Docker Desktop running on Windows host
- Target DFW region restaurants as initial dataset for validation and backtesting

---

## Current Focus
> Update this section as the project progresses.

**Phase 1 — COMPLETE**
- Scaffolding, docker-compose, Google Places scraper end-to-end
- Raw signals confirmed landing in raw_signals table

**Phase 2 — COMPLETE**
- Foursquare scraper (replaced Yelp — cost prohibitive at $229/mo)
- Scoring engine computing review_velocity_score, rating_trend_score, overall_score
- Scores confirmed landing in health_scores table

**Phase 3 — COMPLETE**
- Health inspection scraper (Dallas OpenData + Fort Worth MyHealthDepartment)
- TABC liquor license monitor (Texas Open Data Portal — no API key required)
- Google hours change detector (uses existing Google Places snapshots)
- operational_score added to scoring engine
- Score caps: TABC suspension caps at 40, critical inspection caps at 50

**Phase 3b — COMPLETE**
- Trend analysis added to scoring engine
- health_scores extended with trend fields
- EF Core migration created
- license_history_risk caps overall_score at 65
- API response updated with all trend fields

**Phase 4 — COMPLETE**
- APScheduler wired with all 6 jobs running automatically
- Scheduler runs as container entry point with restart: unless-stopped
- Error handling added across all scrapers
- Test cycle confirmed: Pecan Lodge overall_score=93

**Phase 5 — COMPLETE**
- Bulk CSV onboarding pipeline
- Auto-lookup of Google Place ID and Foursquare ID per restaurant
- Dynamic scheduler picks up new restaurants every 10 minutes
- Idempotent onboarding confirmed (35/35 steps green)
- 5 DFW restaurants onboarded: Pecan Lodge 93, The Rustic 93, Torchy's Tacos 96, Uchi Dallas 97
- New endpoints: POST /onboard, GET /restaurants, GET /restaurants/{id}

**Phase 6 — COMPLETE**
- Single page HTML demo at /demo/index.html
- Three level score display: summary, signal breakdown, raw evidence
- Score factors emitted by scoring engine and stored as JSONB
- Restaurant search by name + address + city with disambiguation
- Recently scored list and side-by-side comparison with difference highlighting
- New endpoint: POST /api/v1/restaurants/search-and-score with 24hr caching

**Phase 6b — COMPLETE**
- Seasonality adjustment module using DFW industry seasonal factors
- Year-over-year comparison when 12 months of history available
- Period comparison with seasonal normalization as fallback
- New score factors: monthly volume trend, recency gap, owner response rate,
  1-star spike, 90-day rating slope, recent vs lifetime gap,
  cross-source divergence, review count confidence
- Warning badges in demo UI for triggered flags
- Monthly review sparkline in demo UI

**Phase 6c — COMPLETE**
- Confidence gate added: minimum 10 data points required before applying trend penalties
- monthly_volume_trend shows insufficient_data instead of false sharply_declining signals
- Same gate applied to ninety_day_slope, recent_vs_lifetime_gap, owner_response_rate
- volume_trend_confidence column added to health_scores
- Demo UI shows gray neutral indicator for insufficient_data with explanatory tooltip
- No healthy restaurant shows red warning badges due to insufficient data

**Phase 7 — COMPLETE**
- Outscraper Google Maps Reviews integration: fetches up to 12 months of review history per restaurant
- monthly_breakdown stored in raw_signals (source=outscraper_reviews) with count + avg_rating per month
- Scoring engine prefers Outscraper data for year_over_year comparison; falls back to period_comparison
- comparison_method = year_over_year when Outscraper data available; monthly_volume_trend shows real trend
- Health inspection coverage expanded to all 10 DFW cities with per-city authority routing:
  Dallas (City of Dallas), Fort Worth/Arlington/Grand Prairie (Tarrant County MHD),
  Plano/Frisco/McKinney (Collin County MHD), Irving/Garland (Dallas County), Denton (Denton County)
- TABC scraper confirmed state-wide: covers all DFW cities, city_matched logged per record
- Outscraper job added to scheduler: weekly Sunday 1:00 AM UTC (CronTrigger, before 5 AM scoring)

**Phase 7b — COMPLETE**
- Google Places scraper migrated from legacy API to Places API v1
- Endpoint: places.googleapis.com/v1/places/{place_id} with X-Goog-Api-Key header and X-Goog-FieldMask
- Response normalized to legacy schema (user_ratings_total, opening_hours, etc.) — scoring engine and hours monitor unchanged
- publishTime (ISO 8601) replaces legacy time (Unix int); _review_dt() handles both for backward compatibility
- api_version=v1 stored in all new raw_signals payloads
- test_google_places_v2.py updated to run live scrape and verify publishTime parsing

**Phase 8 — Next (choose one)**
Option A: API hardening — authentication, rate limiting, ready for first paying customer
Option B: Customer validation — demo to Sysco/distributor contact, gather feedback
Option C: Job postings signal — integrate Indeed or LinkedIn for staffing stress indicator
Option D: Website uptime monitor — direct HTTP check as low-weight operational signal