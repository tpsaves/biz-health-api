Read CLAUDE.md for full project context before doing anything.
Add a monthly usage cap of 10,000 records to the Outscraper reviews scraper to control costs. At $3 per 1,000 records this caps monthly Outscraper spend at $30.
1. Create /scrapers/signals/outscraper_quota.py that:

Tracks monthly Outscraper record usage in a new outscraper_usage table
Provides a check_quota(records_requested) function that:

Queries total records fetched this calendar month from the usage table
Returns allowed: true if usage + records_requested <= 10,000
Returns allowed: false with remaining: N if the cap would be exceeded


Provides a log_usage(restaurant_id, records_fetched) function that inserts
a usage record after each successful Outscraper call
Provides a monthly_summary() function that returns:
json{
  "month": "2026-05",
  "records_used": 4200,
  "records_remaining": 5800,
  "cap": 10000,
  "estimated_cost": "$12.60",
  "pct_used": 42
}


2. Create the usage tracking table:
sqlCREATE TABLE outscraper_usage (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id     uuid REFERENCES restaurants(id),
  records_fetched   int,
  month             varchar,    -- format: YYYY-MM
  scraped_at        timestamptz DEFAULT now()
);
Create EF Core migration for the new table.
3. Update /scrapers/signals/outscraper_reviews.py to:

Call check_quota(30) before each restaurant scrape
If quota allows: proceed with scrape, call log_usage() after success
If quota exceeded: skip the scrape, log a warning:
"Outscraper monthly cap reached (10,000 records) — skipping {restaurant_name}"
Write a outscraper_quota_exceeded entry to raw_signals so the skip is visible
If remaining quota is less than 30 (one restaurant's worth): reduce
reviewsLimit to match remaining quota rather than skipping entirely

4. Update /scrapers/scheduler.py to:

Print monthly Outscraper usage summary at scheduler startup
Print remaining quota before each Sunday Outscraper batch run
If quota is already exhausted skip the entire batch and log clearly

5. Add quota endpoint to the .NET API:

GET /api/v1/admin/outscraper-quota — returns current month usage summary

6. Add quota display to /demo/index.html in a small admin section at the bottom:

Shows current month usage: "Outscraper: 4,200 / 10,000 records used ($12.60)"
Progress bar showing percentage used
Color: green under 70%, yellow 70-90%, red over 90%
Updates when the page loads

7. Create /scrapers/signals/test_outscraper_quota.py that:

Checks current monthly usage
Simulates a quota check for 30 records
Prints monthly summary
Confirms usage table exists and is populated from previous scrape runs

Test inside Docker:
docker-compose exec scrapers python signals/test_outscraper_quota.py
Success condition:

Usage table populated with historical scrape records
Monthly summary shows accurate record count and estimated cost
check_quota() correctly allows or blocks based on remaining quota
Scheduler prints quota summary on startup
Demo UI shows usage meter at the bottom