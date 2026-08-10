# Small Business Financial Health Signals API
## Vertical: Restaurants & Food Service (DFW Region)
## Product Name: Tableside API

## ⚠️ NAMING — CANONICAL
- **Tableside API** is the canonical customer-facing product name. Use it in all UI,
  demos, PDFs, outreach, and anything a customer sees.
- **BizHealth** is the internal project/folder name only (the repo folder is biz-health-api,
  the database is named bizhealth, the .NET solution is BizHealthApi). These are internal
  identifiers — do not rename them, but never show them to customers.
- If any customer-facing surface still says "BizHealth," it should be changed to "Tableside API."

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
      google_places.py         # Google Places API v1 — stores full response, types, businessStatus
      foursquare.py            # Inactive — free tier returns no venue details
      health_inspections.py    # All 10 DFW cities — confidence classification
      tabc_license.py          # Texas Open Data Portal — confidence classification
      hours_monitor.py         # Now computes total_weekly_hours + hours_reduction_pct
      outscraper_reviews.py    # Primary review source — biweekly, stores FULL reviews_data
      outscraper_quota.py      # Monthly usage cap — 15,000 records/month with safety threshold
      sba_loans.py
      property_tax.py
      delivery_platforms.py    # platform_unavailable in Docker (bot detection)
    /scoring
      engine.py
      engine_v2.py             # Current active engine
      seasonality.py
      keyword_analyzer.py
      signal_confidence.py
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
      holdout_validation.py     # Out-of-time train/test split with leakage audit
      lead_time_analysis.py     # Early warning lead time exhibit
      band_outcome_table.py     # Score-band to outcome-rate table
  /demo
    index.html
  /db
    init.sql
    /migrations
      outscraper_run_log.sql
  /backtesting_results
    report_{date}.json
    holdout_validation.json
    methodology_notes.md
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
- **Connection (.NET)**: Host=db;Port=5432;Database=bizhealth;Username=admin;Password=...
- **Connection (Python)**: postgresql://admin:password@db:5432/bizhealth

### Key Tables

**restaurants** — name, address, city, state, zip, google_place_id, foursquare_place_id, phone, website

**raw_signals** — source, payload JSONB, scraped_at UTC

**health_scores** — see full column list below

**outscraper_usage** — id, restaurant_id, records_fetched, month, scraped_at

**outscraper_run_log** — id, run_date, status, restaurants_completed, restaurants_skipped,
restaurants_no_reviews (nullable), restaurants_failed (nullable),
records_used_before, records_used_after, created_at

**closed_restaurants** — id, name, address, city, zip, google_place_id, yelp_id,
closure_date, closure_date_estimated, closure_source, created_at

**backtest_cohort** — id, restaurant_id, cohort_type, baseline_score, baseline_risk_band,
baseline_date, baseline_factors JSONB, outcome_90d, outcome_180d, outcome_90d_date,
outcome_180d_date, closure_date, closure_source, notes, created_at

### health_scores Columns
```sql
-- Score components
review_velocity_score, rating_trend_score, operational_score, staffing_score,
financial_risk_score, overall_score,
-- Trend analysis
license_history_risk, inspection_trend, hours_change_count, last_inspection_date,
last_inspection_score, license_status, license_expiry_date,
-- Enhanced velocity and rating
review_gap_alert, one_star_spike, rating_deterioration, source_divergence,
ninety_day_slope, days_since_last_review, owner_response_rate, monthly_volume_trend,
review_count_confidence, seasonality_adjusted, comparison_method, volume_trend_confidence,
recency_source,
-- Financial risk
sba_default, repeated_sba_borrowing, tax_delinquent, sba_loan_count,
sba_latest_status, sba_latest_amount, tax_delinquency_years,
-- Delivery platforms
doordash_listed, ubereats_listed, delivery_platform_count, delivery_status, delivery_platform_loss,
-- Composite risk cap (NOTE: known leakage — see Validation Status)
composite_risk_cap,
-- Enhanced rating trend
pct_5star_recent, pct_1star_recent, high_negative_rate, negative_rate_rising,
bimodal_distribution, sanitation_flag, operational_instability_flag, ownership_change_flag,
quality_decline_flag, financial_stress_flag, keyword_findings, response_rate_declining,
owner_disengaged, response_rate_recent, response_rate_prior,
-- Signal confidence
tabc_confidence, tabc_confidence_reason, inspection_confidence, inspection_confidence_reason,
tabc_expected_missing, inspection_expected_missing, inspection_data_unavailable,
-- Phase 14 operational signals
business_status, temporarily_closed, permanently_closed,
total_weekly_hours, hours_reduction_pct, hours_reduction,
-- Score factors
score_factors JSONB, scored_at timestamptz
```

### Score Caps
- Active TABC suspension/expiration: caps overall_score at 40
- Critical health inspection failure (score < 60): caps at 50
- license_history_risk true: caps at 65
- sba_default true: caps at 45
- tax_delinquent 2+ years: caps at 55
- Composite risk cap (operational < 65 AND velocity < 30): caps at 59 — **KNOWN LEAKAGE, see below**
- PERMANENTLY_CLOSED: caps at 20 (Phase 14)
- TEMPORARILY_CLOSED: -30 operational_score (Phase 14)
- hours_reduction >= 30%: -15 operational_score (Phase 14)

### overall_score Weights
- review_velocity_score: 20%, rating_trend_score: 30%, operational_score: 30%, financial_risk_score: 20%

### Score Risk Bands
- 80-100: Low (green) | 60-79: Moderate (yellow) | 40-59: Elevated (orange) | 0-39: High (red)

---

## ⚠️ VALIDATION STATUS — READ BEFORE MAKING ANY ACCURACY CLAIMS

**Do NOT cite "100% precision and recall" anywhere — to customers, in the demo, or in documents.**
That number was inflated by data leakage and is retired.

### What happened
The composite risk cap (operational < 65 AND velocity < 30 → cap at 59) was tuned by observing
the exact 3 test-set restaurants (STEEL, MI PUEBLITO, THE LAST STAND) it was then evaluated on.
This is data leakage. A model-risk reviewer will catch it immediately.

### Honest out-of-time holdout results (holdout_validation.py)
- Train set (closures 2017-2021, n=5): 100% recall — these scored 36-46 naturally, no cap needed
- Test set (closures 2022-2023, n=3), WITH composite cap: 100% recall — LEAKAGE, do not cite
- Test set, WITHOUT composite cap (HONEST): **0% recall** — all 3 scored 58-59 (moderate band), missed
- Leakage audit: 9 rules, 8 PASS, 1 FAIL (composite cap only)

### What the model genuinely does well (leakage-free)
- **Lead time**: every closure the model caught, it caught 180+ days in advance (lead_time_analysis.py)
  - Score < 60: 8/8 closures flagged, avg lead time >= 180 days, 100% flagged 90+ days early
  - Score < 40: 4/8 closures flagged
- Catches HARD failures reliably (restaurants that decline across all signals)

### What the model cannot yet prove
- Catching SOFT failures — restaurants that close while maintaining 4.0+ star ratings
- The 3 missed test restaurants kept strong ratings (rating_trend 68-87) which held overall score above 60

### Phase 14 signal additions (forward-looking, cannot fix historical recall)
- businessStatus (TEMPORARILY_CLOSED appears 30-90 days before permanent closure)
- hours-per-day reduction (engine previously only counted days open, not hours per day)
- These are domain-knowledge rules (PASS leakage audit) but couldn't be measured on 2022-2023
  closures because the data wasn't collected then. Impact on historical recall: none (data gap)

### The real validation is the prospective cohort
- 94 restaurants, scored and LOCKED June 1, 2026
- 90-day outcomes due September 1, 2026 — this is the first leakage-free out-of-time validation
- Because the model is locked before outcomes are known, this number cannot be gamed

### Honest customer framing
Lead with lead time and the locked prospective cohort. Say: "I'd rather show you a real number
in September than an inflated one today." This candor is persuasive to credit professionals.

### Known structural limitation
Financial signals (SBA, property tax) are LAGGING indicators — months to years of non-payment
before appearing in public records. They will not catch fast declines. Future signal: lease/
eviction court records (different data source, not a scoring fix).

### The "bad" definition problem (from validation framework review)
Current backtest uses restaurant CLOSURE as the outcome. A distributor cares about PAYMENT DEFAULT
(write-offs, 90+ days past due) — not closure. These correlate but are not the same. The real
validation study requires a design partner's receivables data. Closure is a proxy until then.

---

## API Service (.NET)

### Key Endpoints
- GET /health
- GET /api/v1/restaurants — paginated list with latest overall_score
- GET /api/v1/restaurants/{id} — full details with score breakdown
- POST /api/v1/restaurants — register restaurant
- POST /api/v1/restaurants/onboard — name + address, triggers lookup and onboarding
- POST /api/v1/restaurants/search-and-score — name + address + city, cached or fresh in 30s
- GET /api/v1/backtesting/summary — accuracy report
- GET /api/v1/backtesting/cohort — paginated cohort with outcomes
- GET /api/v1/backtesting/holdout — out-of-time test set results
- GET /api/v1/backtesting/lead-time — early warning lead time summary
- GET /api/v1/backtesting/band-table — score band to outcome rate table
- GET /api/v1/admin/outscraper-quota — current month usage, backfill separated
- GET /api/v1/admin/outscraper-runs — last 10 run history

---

## Scraper Service (Python)

### ⚠️ DATA STORAGE POLICY — NON-NEGOTIABLE
Raw data paid for or fetched from any external source MUST be stored in full in raw_signals.
Aggregations are computed ON TOP of raw data, never INSTEAD of it.
ALWAYS store complete API response, every field, individual records as arrays.
THEN compute aggregations as additional payload keys.
NEVER store only aggregated output, filter responses, or optimize payload size at expense of completeness.
Background: Phase 2 Outscraper scraper discarded reviews_data, losing 46,488 paid reviews. Fixed Phase 11.

### Signal Sources
| Signal | Weight | Source | Key | Notes |
|---|---|---|---|---|
| Google review velocity | High | Google Places API v1 | Yes | Full response + types + businessStatus |
| Google rating trend | High | Google Places API v1 | Yes | |
| Outscraper reviews | High | Outscraper | Yes | Full reviews_data, 70 reviews/restaurant |
| Foursquare rating | Medium | Foursquare | Yes (fsq3...) | Inactive |
| Health inspections | High | City routing table | No | Confidence classification |
| TABC license | High | Texas Open Data Portal | No | Confidence classification |
| Hours consistency | Medium | Google Places snapshots | No | Now total_weekly_hours |
| SBA loan history | High | SBA Data.gov API | No | Fuzzy matching min 80, LAGGING |
| Property tax | High | County CAD portals | No | LAGGING, may return no_data |
| Delivery platforms | Medium | DoorDash + Uber Eats | No | platform_unavailable in Docker |
| businessStatus | High | Google Places API v1 | Yes | Phase 14 — temp/perm closed |
| Hours reduction | Medium | hours_monitor periods diff | No | Phase 14 — hours/day not days |
| Job postings | Medium | Placeholder | TBD | |
| Website uptime | Low | Direct HTTP check | No | |

### Health Inspection City Routing
Dallas → dallas portal | Fort Worth/Arlington/Grand Prairie → tarrant portal |
Plano/Frisco/McKinney → plano (Collin) portal | Irving/Garland/Denton → no_inspection_data

### Property Tax CAD Routing
Dallas/Irving/Garland → Dallas CAD | Fort Worth/Arlington/Grand Prairie → Tarrant CAD |
Plano/Frisco/McKinney → Collin CAD | Denton → Denton CAD

### Signal Confidence States
- confirmed — record found and matched
- not_applicable — business type does not require this signal
- expected_not_found — record expected from Google Place types but not found (risk flag)
- unknown — cannot determine whether record should exist

### Outscraper Configuration
- 70 reviews/restaurant, full reviews_data stored
- Biweekly: 1st and 15th of month at 1:00 AM UTC
- Projected: 94 × 70 × 2 = 13,160 records/month (~$39.48)
- Hard cap: 15,000/month (outscraper_quota.py), safety threshold 500
- Throttle: partial run if remaining < projected, prioritizing least-recently scraped
- Backfill complete: one-time 200-review pull for May 25+ history, exempt from cap
- 94 restaurants total; 8 excluded per run (no google_place_id — not Google-onboarded), leaving 86 eligible
- May 2026: 4,975 regular + 27,973 backfill = 32,948 total

### Outscraper Run-Log Semantics

Outcome taxonomy for each restaurant in the batch:
- **completed** — Outscraper returned >=1 review. Writes a `raw_signals` row.
- **no_reviews** — Outscraper queried successfully but returned an empty array. Still writes a
  `raw_signals` row (payload has `reviews_data: []`, `total_reviews_fetched: 0`) per the
  raw-data policy — an empty result is a real, stored signal, never dropped.
- **quota_hit** — internal monthly record cap reached mid-run. No `raw_signals` row.
- **no_credits** — Outscraper account balance exhausted (HTTP 402). No `raw_signals` row.
- **failed** — an exception was raised for that restaurant. No `raw_signals` row.

Status invariant: **`ok` means zero failures AND zero skips** — everything that could land, did.
Any exception or skip breaks `ok`. Status values: `ok`, `throttled` (partial: some landed, some
skipped/failed), `no_credits`, `quota_exhausted`, `failed`, `empty` (no eligible restaurants).

Run-log columns:
- `restaurants_completed` counts only actual reviews-fetched results — a live tally, never the
  size of the eligible set. (Historical rows predating this fix log a flat `86` and are stale;
  do not trust `completed` on pre-fix rows.)
- `restaurants_skipped` = quota_hit + no_credits only.
- `restaurants_no_reviews` and `restaurants_failed` are their own **nullable** columns. NULL on
  historical rows means "not tracked when this run happened" — never backfill with 0.

Reconciliation (SQL-only): for any run written by the current code,
`COUNT(DISTINCT restaurant_id)` in `raw_signals` for that run's date
== `restaurants_completed + COALESCE(restaurants_no_reviews, 0)`.
`failed` and `skipped` are excluded from both sides because they leave no `raw_signals` footprint.
When run-log and raw_signals disagree, **raw_signals is the source of truth.**

Exception isolation: the per-restaurant loop wraps each restaurant in try/except; a single
failure increments `failed` and continues to the next. One bad restaurant must never abort the
batch.

### Outscraper Limits — Two Distinct Ceilings

The external account credit balance (402 PAYMENT REQUIRED) and the internal 15,000-record
monthly cap (quota guard, tracked in `outscraper_usage`) are separate limits. The internal
guard can report budget remaining (e.g. `0/15000 used`) while the account is actually out of
credits. When a run pulls zero, check the Outscraper dashboard balance — not just the internal
quota — before assuming the pipeline is broken.

### Review Data Priority
- Sparkline/velocity: Outscraper monthly_breakdown preferred, Google fallback
- Recency: minimum of Outscraper and Google timestamps
- Keyword analysis + rating distribution + response rate: Outscraper reviews_data

### Seasonality (seasonality.py)
Jan 0.80, Feb 0.90, Mar 1.05, Apr 1.10, May 1.10, Jun 1.05,
Jul 0.95, Aug 0.90, Sep 1.05, Oct 1.15, Nov 1.10, Dec 1.15

### Scraper Schedule
- Google Places: daily 2 AM | Hours monitor: daily 3 AM | Scoring engine: daily 5 AM
- Outscraper: biweekly 1st/15th 1 AM | SBA: weekly Sun 1:30 AM | Property tax: weekly Sun 2 AM
- Outcome tracker: weekly Sun 3 AM | Delivery: weekly Mon 5 AM
- Health inspections + TABC: weekly Mon 4-4:30 AM | New restaurant check: every 10 min

### Scheduler Reliability (Known Risk)

APScheduler cron jobs run inside the scrapers container. If the container is down at fire time
(e.g. Docker Desktop off/asleep), the job is silently dropped — no error, no catch-up. Manual
backfill is required after any outage. TODO (not yet implemented): set `misfire_grace_time` and
`coalesce=True` so a short-missed job runs on restart; longer term, move the scheduler to an
always-on host rather than a dev laptop.

---

## Backtesting Framework

### Current Status (May 2026)
- Closed restaurant dataset: 72 verified closed DFW restaurants
- Retrospective: 9 restaurants scored at T-90/T-180 (n=9, leakage in composite cap)
- Prospective cohort: 94 restaurants, locked June 1 2026
- 90-day outcomes due September 1 2026 (first leakage-free validation)
- 180-day outcomes due December 1 2026

### Prospective Cohort Distribution
Low: 7 | Moderate: 47 | Elevated: 51 | High: 2 | Total: 94 (band breakdown at lock, pre-removal)

### Honest Model Performance
- Out-of-time test recall (leakage-free): 0% on n=3 — model misses soft/moderate-band closures
- Lead time (leakage-free): 180+ days average advance warning on caught closures
- See VALIDATION STATUS section above for full honest framing

### Demo Restaurants
- Healthy: Pecan Lodge, 2702 Main St, Dallas — score 93
- Distressed: Backyard Dallas — score 51, TABC_MISSING + INSP_MISSING, 89 days no review,
  sharply declining volume, 0% owner response, 3/7 days open
- Backup distressed: The Free Man Cajun Cafe, 2630 Commerce St, Dallas

---

## Demo UI (http://localhost:3000)

- nginx served, must use http://localhost:3000 not file://
- Search: Name (required), Address or Zip (required), City (optional dropdown)
- Four component score bars: Review Velocity 20%, Rating Trend 30%, Operational 30%, Financial Risk 20%
- Dual line YoY review chart with hover crosshair, YoY%, seasonally adjusted %
- Rating trend drill-down: distribution bar, keyword chips, owner engagement row
- Signal confidence display: confirmed / expected_not_found (red) / not_applicable (gray) / unknown (gray)
- Recently scored list: clickable, auto-populates, active highlight
- Side-by-side comparison, disambiguation list, plain English recommendations
- Backtesting tab: out-of-time results, lead time, score-band table (proxy-outcome labeled)
- Admin footer: Outscraper usage meter, run history dots, projected cost

### Warning Badges
review_gap_alert, one_star_spike, rating_deterioration, source_divergence, sba_default,
repeated_sba_borrowing, tax_delinquent, delivery_platform_loss, composite_risk_cap (orange),
sanitation_flag (red), owner_disengaged (orange), bimodal_distribution (orange),
ownership_change_flag (yellow), tabc_expected_missing (red "License Missing"),
inspection_expected_missing (orange), temporarily_closed (red), hours_reduction (orange)

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
- New to Python — inline comments on patterns differing from C#
- Docker Desktop on Windows host
- 94 restaurants in prospective cohort (86 eligible per run — 8 excluded for no google_place_id)
- Pecan Lodge google_place_id: ChIJGXYxd92YToYR7yV_BSMQ2Xk

---

## Operations

### Destructive SQL Discipline

Never run UPDATE/DELETE on log tables without first running a SELECT dry-run with the IDENTICAL
WHERE clause and confirming the exact row count. Target rows by UUID primary key (`id IN (...)`),
not by value filters like status or count — those can drift onto legitimate rows when multiple
rows share the same values.

### Windows / PowerShell Operational Notes

- `psql` is not on the Windows PATH; run it via `docker exec -e PGPASSWORD=... <db-container>
  psql -U admin -d bizhealth -c "..."`.
- PowerShell reserves `<`. Never use `psql -f /dev/stdin < file.sql`. Run migrations with inline
  `-c "..."` or `-f` pointing at a path inside the container.
- Large prompts to Claude Code: pasted input is capped (~5KB) and multi-line paste can corrupt in
  the classic console. Use the standing pattern — drop a `.md` in the project root and tell Claude
  Code to `read <file> and follow the instructions in it`. Prefer Windows Terminal over the
  classic console host for pasting.

---

## Current Focus

**Phases 1-11 — COMPLETE** (scaffolding through demo UI polish, cost controls, signal confidence)

**Phase 12 — Customer Validation prep — COMPLETE**
- LinkedIn outreach drafted, 20-min demo script, customer validation PDF generated

**Phase 13 — Honest Validation — COMPLETE**
- Out-of-time holdout validation built (holdout_validation.py)
- Exposed composite cap leakage: honest test recall is 0%, not 100%
- Lead-time analysis: 180+ days advance warning (leakage-free, genuine)
- Score-band outcome table (proxy-outcome labeled)
- methodology_notes.md documents all caveats
- Retired the "100% precision/recall" claim everywhere

**Phase 14 — Signal Gap Fixes — COMPLETE**
- Added businessStatus (TEMPORARILY_CLOSED / PERMANENTLY_CLOSED) from Google Places
- Added hours-per-day reduction detection (was only counting days open)
- Both domain-knowledge rules, PASS leakage audit
- Cannot retroactively fix 2022-2023 recall (data wasn't collected then)
- Forward-looking value: catches soft failures the model previously missed

**Phase 15 — Customer Validation + Design Partner (current)**
- PDF and demo script reframed around lead time + locked prospective cohort (honest)
- Primary goal: find a design partner willing to share anonymized receivables data
  — this unlocks the REAL validation study (payment default, not closure proxy)
- Send first 3 LinkedIn outreach messages
- September 1 2026: first leakage-free prospective cohort outcomes
- Do NOT build more signals — the model is as good as it gets without real outcome data
- Validation study sections (bad-rate tables, swap-set, dollars-saved, PAYDEX benchmark)
  are co-produced WITH design partner data, not before
