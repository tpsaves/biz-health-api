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
  /db
    init.sql
```

### Docker Compose
- All services run via docker-compose
- The `scrapers` service populates data; the `api` service reads it
- Services communicate via shared PostgreSQL 17 instance, not directly
- Postgres hostname inside Docker network is `db`
- Postgres is exposed to Windows host at `localhost:5432` for GUI tools (TablePlus, DBeaver)

---

## Database

- **Engine**: PostgreSQL 17
- **ORM (.NET)**: EF Core with Npgsql provider
- **ORM (Python)**: SQLAlchemy
- **Connection string format (.NET)**: `Host=db;Port=5432;Database=bizhealth;Username=admin;Password=...`
- **Connection string format (Python)**: `postgresql://admin:password@db:5432/bizhealth`

### Key Tables
- `restaurants` — master record per restaurant (name, address, google_place_id, yelp_id, etc.)
- `raw_signals` — raw scraped data before processing (source, payload JSONB, scraped_at UTC)
- `health_scores` — computed scores per restaurant (component scores, overall_score, scored_at UTC)

### health_scores Columns
```sql
review_velocity_score   int,   -- Google review velocity trend
rating_trend_score      int,   -- Google/Yelp rating trend over 90 days
operational_score       int,   -- Hours consistency, website uptime
staffing_score          int,   -- Hiring activity trends
overall_score           int,   -- Weighted composite score
scored_at               timestamptz
```

### Conventions
- All tables use snake_case
- All timestamps are UTC
- Raw scraped payloads stored as JSONB before normalization
- No localhost in connection strings — always use service name `db` inside containers

---

## API Service (.NET)

### Conventions
- .NET 9 minimal API or controller-based (decide at scaffolding)
- EF Core for all database access
- Follow standard .NET naming conventions (PascalCase classes, camelCase JSON output)
- Return `ProblemDetails` for error responses
- All endpoints versioned under `/api/v1/`
- Connection strings loaded from environment variables via `.env`

### Key Endpoints (planned)
- `GET /api/v1/restaurants/{id}/score` — return latest health score
- `GET /api/v1/restaurants/search?name=&zip=` — find a restaurant
- `POST /api/v1/restaurants` — register a restaurant for tracking
- `GET /health` — health check endpoint (returns 200)

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
| Signal | Weight | Notes |
|---|---|---|
| Google review velocity | High | Leading indicator — goes quiet before closure |
| Google rating trend | High | Declining rating over 90 days is a strong warning signal |
| Yelp review count + status | Medium | Cross-validates Google data |
| Business hours consistency | Medium | Reduced hours often precede closure |
| Job postings | Medium | Hiring kitchen staff = growing, cutting = struggling |
| Website uptime | Low | Many restaurants don't maintain sites |
| Social activity | Low | Inconsistent signal across demographics |

### Scraper Module Pattern
```
/scrapers/signals/
  google_places.py      ← Build first
  yelp.py
  job_postings.py
  website_monitor.py
```

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
YELP_API_KEY=
ANTHROPIC_API_KEY=

ASPNETCORE_ENVIRONMENT=Development
```

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

**Phase 2 — Foursquare scraper + scoring model (current)**
- Build Foursquare scraper writing to raw_signals
- Build scoring engine that reads raw_signals and writes computed scores to health_scores
- review_velocity_score and rating_trend_score from Google data
- Cross-validate rating with Foursquare data

**Phase 3 — Job postings signal (planned)**
