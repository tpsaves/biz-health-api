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
| `demo` | Single page HTML/CSS/JS | Customer-facing demo UI |

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
      seasonality.py    ← DFW seasonal adjustment factors and normalization functions
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
- Services communicate via shared PostgreSQL 17 instance, not directly
- Postgres hostname inside Docker network is `db`
- Postgres is exposed to Windows host at `localhost:5432` for GUI tools (TablePlus, DBeaver)
- API runs on port 8080 (not 5000)
- Scrapers container restarts automatically (`restart: unless-stopped`)

---

## Database

- **Engine**: PostgreSQL 17
- **ORM (.NET)**: EF Core with Npgsql provider
- **ORM (Python)**: SQLAlchemy
- **Connection string format (.NET)**: `Host=db;Port=5432;Database=bizhealth;Username=admin;Password=...`
- **Connection string format (Python)**: `postgresql://admin:password@db:5432/bizhealth`

### Key Tables

**`restaurants`** — master record per restaurant
- name, address, city, state, zip, google_place_id, foursquare_place_id, phone, website

**`raw_signals`** — raw scraped data before processing
- source (google_places, foursquare, health_inspection, tabc_license, hours_monitor)
- payload JSONB
- scraped_at UTC

**`health_scores`** — computed scores per restaurant
```sql
-- Score components
review_velocity_score   int,
rating_trend_score      int,
operational_score       int,
staffing_score          int,    -- null, placeholder for future job posting signal
overall_score           int,

-- Trend analysis fields (added Phase 3b)
license_history_risk    boolean,  -- true if suspended/expired in last 90 days even if now active
inspection_trend        varchar,  -- improving, declining, stable, insufficient_data
hours_change_count      int,      -- number of hours changes in last 90 days
last_inspection_date    date,
last_inspection_score   int,
license_status          varchar,  -- current TABC license status
license_expiry_date     date,

scored_at               timestamptz

-- Score factors (added Phase 6)
score_factors           JSONB,   -- structured explanation of what drove each component score

-- Enhanced velocity and rating fields (added Phase 6b)
review_gap_alert          boolean,
one_star_spike            boolean,
rating_deterioration      boolean,
source_divergence         boolean,
ninety_day_slope          varchar,  -- improving, stable, declining, sharp_decline
days_since_last_review    int,
owner_response_rate       int,      -- percentage 0-100
monthly_volume_trend      varchar,  -- growing, stable, declining, sharply_declining
review_count_confidence   varchar,  -- high, medium, low
seasonality_adjusted      boolean,  -- true if seasonal normalization was applied
comparison_method         varchar,  -- year_over_year or period_comparison

```

### Score Caps
- Active TABC suspension or expiration → caps `overall_score` at 40
- Critical health inspection failure (score < 60 or imminent hazard) → caps `overall_score` at 50
- `license_history_risk` true → caps `overall_score` at 65

### overall_score Weights
- `review_velocity_score` 25%
- `rating_trend_score` 35%
- `operational_score` 40%

### Score Risk Bands
- 80-100: Low risk (green)
- 60-79: Moderate risk (yellow)
- 40-59: Elevated risk (orange)
- 0-39: High risk (red)

### Conventions
- All tables use snake_case
- All timestamps are UTC
- Raw scraped payloads stored as JSONB before normalization
- No localhost in connection strings — always use service name `db` inside containers

---

## API Service (.NET)

### Conventions
- .NET 9 Web API with EF Core
- Follow standard .NET naming conventions (PascalCase classes, camelCase JSON output)
- Return `ProblemDetails` for error responses
- All endpoints versioned under `/api/v1/`
- Connection strings loaded from environment variables via `.env`

### Key Endpoints
- `GET /health` — health check (returns 200)
- `GET /api/v1/restaurants` — paginated list of all tracked restaurants with latest overall_score
- `GET /api/v1/restaurants/{id}` — full restaurant details including latest score breakdown
- `POST /api/v1/restaurants` — register a restaurant for tracking
- `POST /api/v1/restaurants/onboard` — accepts name and address, triggers lookup and onboarding
- `POST /api/v1/restaurants/search-and-score` — accepts name and city, returns cached or fresh score within 30 seconds

### search-and-score Behavior
- If restaurant exists and scored within last 24 hours → return cached score immediately
- If restaurant exists but score is stale → trigger fresh scoring run and return updated results
- If restaurant does not exist → trigger full onboarding pipeline then return results
- Times out at 30 seconds and returns partial result if scraping takes too long

### Score API Response Shape
```json
{
  "overallScore": 78,
  "reviewVelocityScore": 82,
  "ratingTrendScore": 85,
  "operationalScore": 71,
  "staffingScore": null,
  "licenseStatus": "active",
  "licenseHistoryRisk": false,
  "licenseExpiryDate": "2027-03-15",
  "inspectionTrend": "stable",
  "lastInspectionDate": "2026-02-10",
  "lastInspectionScore": 94,
  "hoursChangeCount": 0
}

{
  "overallScore": 93,
  "operationalScore": 95,
  "scoreFactors": {
    "operational": [
      {
        "signal": "health_inspection",
        "label": "Latest inspection score",
        "value": "94/100",
        "date": "2026-02-10",
        "impact": "positive",
        "weight": "high"
      }
    ],
    "reviewVelocity": [...],
    "ratingTrend": [...]
  }
}

{
  "reviewGapAlert": false,
  "oneStarSpike": false,
  "ratingDeterioration": false,
  "sourceDivergence": false,
  "ninetyDaySlope": "stable",
  "daysSinceLastReview": 4,
  "ownerResponseRate": 42,
  "monthlyVolumeTrend": "stable",
  "reviewCountConfidence": "high",
  "seasonalityAdjusted": true,
  "comparisonMethod": "period_comparison",
  "scoreFactors": {
    "operational": [...],
    "reviewVelocity": [...],
    "ratingTrend": [...]
  }
}```

---

## Scraper Service (Python)

### Conventions
- Follow PEP8
- Each scraper is a separate module under `/scrapers/signals/`
- All scrapers write raw response to `raw_signals` table before any processing
- Use APScheduler for scheduling recurring scrape jobs
- Use `httpx` for async HTTP requests
- Use `python-dotenv` for loading environment variables
- Add comments explaining Python-specific patterns that differ from C#
- Max retries: 3 per job
- Job timeout: 60 seconds per restaurant per scraper
- Failed scraper jobs write error record to raw_signals with source `{source}_error`

### Signal Sources & Weights (Restaurant Vertical)
| Signal | Weight | Source | API Key Required |
|---|---|---|---|
| Google review velocity | High | Google Places API | Yes |
| Google rating trend | High | Google Places API | Yes |
| Foursquare rating | Medium | Foursquare Places API | Yes (fsq3...) |
| Health inspection trend | High | Dallas OpenData + Fort Worth MyHealthDepartment | No |
| TABC license status | High | Texas Open Data Portal | No |
| Hours consistency | Medium | Google Places snapshots (change detection) | No |
| Job postings | Medium | Placeholder — not yet built | TBD |
| Website uptime | Low | Direct HTTP check | No |

### Scraper Schedule
- Google Places + Foursquare: daily at 2:00-2:30 AM UTC
- Hours monitor: daily at 3:00 AM UTC
- Health inspections + TABC license: weekly Monday at 4:00-4:30 AM UTC
- Scoring engine: daily at 5:00 AM UTC
- New restaurant check: every 10 minutes

### Onboarding
- Bulk onboarding via CSV (columns: name, address, city, zip)
- Auto-lookup of Google Place ID and Foursquare ID per restaurant
- Idempotent — re-running upserts existing rows without duplicating data
- Unmatched restaurants written to `unmatched.csv` for manual review

---

## Demo UI

- Single self-contained HTML file at `/demo/index.html`
- No build tools required — pure HTML, CSS, vanilla JavaScript
- Calls .NET API at `http://localhost:8080`
- Features:
  - Restaurant search by name and DFW city
  - Visual score breakdown with color-coded risk indicators
  - Component score bars (review velocity, rating trend, operational health)
  - Signal detail section (inspection score, TABC status, hours, trends)
  - Plain English risk recommendation
  - Recently scored list (last 5 restaurants)
  - Side-by-side comparison of 2 restaurants

---

## Environment Variables

All secrets and config live in `.env` at project root. Never hardcode values.

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=bizhealth
POSTGRES_USER=admin
POSTGRES_PASSWORD=

GOOGLE_PLACES_API_KEY=
FOURSQUARE_API_KEY=        # must start with fsq3...
ANTHROPIC_API_KEY=

ASPNETCORE_ENVIRONMENT=Development
```

Note: Health inspection and TABC license scrapers require no API keys — both use free public data portals.

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
- Score caps added: TABC suspension caps overall_score at 40, critical inspection caps at 50

**Phase 3b — COMPLETE**
- Trend analysis added to scoring engine
- health_scores table extended with trend fields
- EF Core migration created for schema changes
- license_history_risk caps overall_score at 65
- API response updated to include all trend fields

**Phase 4 — COMPLETE**
- APScheduler wired up with all 6 jobs running automatically
- Scheduler runs as container entry point with restart: unless-stopped
- Error handling added across all scrapers
- Test cycle confirmed: Pecan Lodge overall_score=93 across all jobs

**Phase 5 — COMPLETE**
- Bulk CSV onboarding pipeline (name, address, city, zip)
- Auto-lookup of Google Place ID and Foursquare ID per restaurant
- Dynamic scheduler picks up newly onboarded restaurants every 10 minutes
- Idempotent onboarding confirmed (35/35 steps green)
- 5 DFW restaurants onboarded and scoring: Pecan Lodge 93, The Rustic 93, Torchy's Tacos 96, Uchi Dallas 97
- New API endpoints: POST /onboard, GET /restaurants, GET /restaurants/{id}

**Phase 6 — COMPLETE**
- Single page HTML demo at /demo/index.html
- Three level score display: summary, signal breakdown, raw evidence
- Score factors emitted by scoring engine and stored as JSONB
- Restaurant search by name + address + city with disambiguation
- Recently scored list and side-by-side comparison with difference highlighting
- New API endpoint: POST /api/v1/restaurants/search-and-score with 24hr caching

**Phase 6b — COMPLETE**
- Seasonality adjustment module using DFW industry seasonal factors (scoring/seasonality.py)
- Year-over-year comparison when 12 months of history available; period comparison fallback
- New score factors: monthly volume trend, recency gap, owner response rate,
  1-star spike, 90-day rating slope, recent vs lifetime gap,
  cross-source divergence, review count confidence multiplier
- 11 new columns in health_scores; velocity_metrics extracted by google_places.py
- Warning badges in demo UI for triggered flags (review_gap_alert, one_star_spike, etc.)
- Monthly review sparkline with DFW seasonal adjustment note
- test_engine_v4.py confirms all 5 DFW restaurants score with full Phase 6b data
