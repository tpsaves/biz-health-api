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
    requirements.txt
    /signals
      google_places.py
      foursquare.py
      health_inspections.py
      tabc_license.py
      hours_monitor.py
    /scoring
      engine.py
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

---

## Database

- **Engine**: PostgreSQL 17
- **ORM (.NET)**: EF Core with Npgsql provider
- **ORM (Python)**: SQLAlchemy
- **Connection string format (.NET)**: `Host=db;Port=5432;Database=bizhealth;Username=admin;Password=...`
- **Connection string format (Python)**: `postgresql://admin:password@db:5432/bizhealth`

### Key Tables

**`restaurants`** — master record per restaurant
- name, address, google_place_id, foursquare_place_id, yelp_id, etc.

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
```

### Score Caps
- Active TABC suspension or expiration → caps `overall_score` at 40
- Critical health inspection failure (score < 60 or imminent hazard) → caps `overall_score` at 50
- `license_history_risk` true → caps `overall_score` at 65

### overall_score Weights
- `review_velocity_score` 25%
- `rating_trend_score` 35%
- `operational_score` 40%

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
- `GET /api/v1/restaurants/{id}/score` — returns full enriched score response
- `GET /api/v1/restaurants/search?name=&zip=` — find a restaurant
- `POST /api/v1/restaurants` — register a restaurant for tracking

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
```

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
| Social activity | Low | Inconsistent signal | TBD |

### Scraper Schedule (Phase 4 target)
- Google Places + Foursquare: daily
- Health inspections + TABC license: weekly
- Hours monitor: daily (compares last two Google Places snapshots)

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
- health_scores table extended with trend fields (see schema above)
- EF Core migration created for schema changes
- license_history_risk caps overall_score at 65
- API response updated to include all trend fields

**Phase 4 — APScheduler automation (current)**
- Wire all scrapers into APScheduler for recurring runs
- Google Places + Foursquare: daily
- Health inspections + TABC license: weekly
- Hours monitor: daily
- Goal: scrapers run automatically without manual execution
