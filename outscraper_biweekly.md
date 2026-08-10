Read CLAUDE.md for full project context before doing anything.
Update the Outscraper reviews scraper to run biweekly instead of weekly, and optimize the reviews per restaurant to stay within the 10,000 record monthly cap.
1. Calculate the optimal reviews per restaurant

Biweekly = 2 runs per month
107 restaurants × X reviews × 2 runs = under 10,000
X = 10,000 / (107 × 2) = 46 reviews per restaurant per run
Set reviewsLimit to 46 in outscraper_reviews.py
This gives 107 × 46 × 2 = 9,844 records/month — just under the cap

2. Update /scrapers/scheduler.py to change Outscraper schedule:

Remove the current weekly Sunday 1:00 AM UTC job
Add two biweekly jobs:

First run: 1st and 15th of each month at 1:00 AM UTC
Use APScheduler's CronTrigger with day='1,15'


Update the scheduler startup log to show:
"Outscraper: biweekly schedule (1st and 15th), 46 reviews/restaurant, ~9,844 records/month"

3. Update /scrapers/signals/outscraper_quota.py to reflect new expected usage:

Update the monthly summary to show projected usage based on biweekly schedule:
json{
  "month": "2026-05",
  "records_used": 4200,
  "records_remaining": 5800,
  "cap": 10000,
  "estimated_cost": "$12.60",
  "projected_monthly": 9844,
  "schedule": "biweekly (1st and 15th)"
}


4. Update outscraper_reviews.py to:

Change reviewsLimit from 30 to 46
Add a comment explaining the calculation:
# 46 reviews × 107 restaurants × 2 runs/month = 9,844 records (~$29.53/month)

5. Update the demo UI admin footer to show projected monthly usage:

Current: "Outscraper: 4,200 / 10,000 records used ($12.60)"
Updated: "Outscraper: 4,200 / 10,000 used ($12.60) — projected 9,844/month on biweekly schedule"

6. Create /scrapers/signals/test_outscraper_biweekly.py that:

Confirms reviewsLimit is set to 46
Confirms scheduler has biweekly CronTrigger on days 1 and 15
Runs a single restaurant scrape with the new limit
Prints records fetched and confirms quota log updated correctly

Test inside Docker:
docker-compose exec scrapers python signals/test_outscraper_biweekly.py
Success condition:

reviewsLimit confirmed at 46
Scheduler shows biweekly schedule on startup
Single test scrape returns up to 46 reviews
Quota summary shows projected 9,844 records/month
Demo UI footer shows updated projection