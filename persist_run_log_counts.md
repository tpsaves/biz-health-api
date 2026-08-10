# Task: Persist `no_reviews` and `failed` counts to the outscraper_run_log row

Add two new outcome counters — `restaurants_no_reviews` and `restaurants_failed` — to the
`outscraper_run_log` table so run history is reconcilable from SQL alone, instead of only from
stdout. Right now `main.py` computes `no_reviews_count` and `failed_count` but only logs them to
stdout; the DB row carries `completed` and `skipped` only.

**Do NOT change scraping, scoring, or quota logic.** This is schema + logging plumbing only.
**Show me each change before applying it**, and report the discovery findings in Step 0 before
touching anything.

## Design decisions (already made — implement as stated)
- The two new columns are **nullable integers**, and existing rows stay **NULL**. NULL means
  "this run predates the instrumentation — not tracked," which is honest. Do NOT backfill
  existing rows with 0 (that would falsely assert those runs had zero no-review/failed results).
  New rows written going forward always carry a concrete integer (0 or more).
- Column names follow the existing convention (`restaurants_completed`, `restaurants_skipped`):
  → `restaurants_no_reviews`, `restaurants_failed`.

## Step 0 — Discover first, report back, then proceed
Answer these before writing any code:
1. How was the `outscraper_run_log` table originally created — a hand-written SQL migration, or
   an EF Core migration? Check the migrations folder and the EF model snapshot. This determines
   the mechanism in Step 1.
2. Is there an EF Core entity mapped to this table (e.g. `OutscraperRunLog.cs`) and is it
   registered as a DbSet on the DbContext? If yes, how do its existing properties map to columns
   — global snake_case naming convention, or explicit `[Column("...")]` attributes? The new
   properties must mirror whatever `RestaurantsCompleted` / `RestaurantsSkipped` do.
3. How are migrations applied at deploy time — auto-applied on API startup via
   `db.Database.Migrate()`, or manually via `dotnet ef database update`? Report which.
4. Show the current `log_run_status(...)` definition in full (Python) and its INSERT statement.

Report all four, then continue.

## Step 1 — Schema: add two nullable columns
Use the mechanism that MATCHES how the table was created (from Step 0):
- If the table is owned by EF migrations → add the properties (Step 2) first, then
  `dotnet ef migrations add AddOutscraperRunLogOutcomeCounts` and inspect the generated
  migration. It must be an `AddColumn` for two **nullable** ints — NOT a `CreateTable`. If it
  emits `CreateTable`, STOP and tell me: the snapshot is out of sync and we handle it manually.
- If the table was created by raw SQL (EF doesn't own it) → add columns with a matching
  hand-written migration:
  ```sql
  ALTER TABLE outscraper_run_log ADD COLUMN restaurants_no_reviews INTEGER NULL;
  ALTER TABLE outscraper_run_log ADD COLUMN restaurants_failed     INTEGER NULL;
  ```
  and skip `dotnet ef migrations add` entirely (the entity in Step 2 is read-mapping only).

Either way the columns must end up nullable with existing rows NULL.

## Step 2 — EF Core entity
Add two properties to the run-log entity, mirroring the mapping style of the existing
`RestaurantsCompleted` / `RestaurantsSkipped` properties (naming convention vs explicit
`[Column]` — match whatever they use). Use nullable ints so historical NULLs read correctly:
```csharp
public int? RestaurantsNoReviews { get; set; }
public int? RestaurantsFailed { get; set; }
```
If the admin display projects specific columns, add these to that projection so they surface.

## Step 3 — Python writer
Extend `log_run_status(...)` to accept and persist the two counts. Keep it backward-safe with
defaults so nothing else calling it breaks:
- Add params `no_reviews: int = 0` and `failed: int = 0`.
- Add `restaurants_no_reviews` and `restaurants_failed` to the INSERT column list and VALUES,
  bound to the new params.
- Update the single call site in the outscraper job to pass the real counters:
  ```python
  log_run_status(
      status, completed, quota_hit + no_credits_count,
      records_before, fresh["records_used"], session,
      no_reviews=no_reviews_count, failed=failed_count,
  )
  ```

## Constraints
- Additive and nullable only — no changes to existing columns, no NOT NULL, no defaults on the
  new columns.
- Do not alter `scrape_outscraper_reviews`, the scoring engine, or quota enforcement.
- `restaurants_skipped` keeps meaning quota + credits (unchanged). `no_reviews` and `failed`
  are their own columns, not folded into skipped.
- Preserve existing style/indentation.

## Deploy order (important)
Schema BEFORE the Python writer. The updated INSERT references the new columns, so they must
exist first or the next run throws. Sequence:
1. Apply the schema change (Step 1) and confirm the columns exist.
2. Rebuild/redeploy so the updated `main.py` and EF entity load (`docker-compose up -d --build`).
Then the next run populates the columns; historical rows remain NULL.

## Report back after applying
1. The Step 0 findings.
2. The generated migration (or the ALTER SQL) — confirm two nullable int columns, existing rows
   untouched/NULL.
3. The final `log_run_status` signature + INSERT and the updated call site.
4. Confirm this query now reconciles a run entirely from SQL (COALESCE handles NULL historical
   rows), returning matched vs actual with no stdout needed:
   ```sql
   SELECT
     l.restaurants_completed
       + COALESCE(l.restaurants_no_reviews, 0) AS expected_raw_footprint,
     (SELECT COUNT(DISTINCT restaurant_id)
        FROM raw_signals
       WHERE source = 'outscraper_reviews'
         AND scraped_at::date = l.run_date) AS actual_raw_distinct,
     l.restaurants_failed
   FROM outscraper_run_log l
   WHERE l.run_date = CURRENT_DATE
   ORDER BY l.created_at DESC
   LIMIT 1;
   ```
   `expected_raw_footprint` should equal `actual_raw_distinct` for any run written by the new code.
