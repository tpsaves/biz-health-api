Read CLAUDE.md for full project context before doing anything.
Two updates to the Outscraper configuration:
1. Increase monthly cap from 10,000 to 15,000 records
Update /scrapers/signals/outscraper_quota.py:

Change monthly cap from 10,000 to 15,000
Update projected_monthly calculation comment
Update estimated cost: 15,000 records × $0.003 = $45/month

Update /scrapers/signals/outscraper_reviews.py:

Recalculate optimal reviews per restaurant:
15,000 / (107 restaurants × 2 runs/month) = 70 reviews per restaurant
Change _MAX_REVIEWS from 46 to 70
Update the budget comment:
# 70 reviews × 107 restaurants × 2 runs/month = 14,980 records (~$44.94/month)

Update the .NET API /api/v1/admin/outscraper-quota response to reflect new cap and projected usage.
Update the demo UI admin footer to show updated projection.
2. Backdate the biweekly schedule — treat March 25th as the effective start date
The goal is to backfill review history as if scraping had started March 25th.
Outscraper returns reviews sorted by date — if we request 70 reviews per restaurant
we'll capture reviews going back further than the current ~46 review window.
Update /scrapers/scheduler.py:

Keep the biweekly CronTrigger on 1st and 15th of month
Add a one-time backfill job that runs immediately on scheduler startup IF
the earliest outscraper_reviews raw_signal for a restaurant is dated after March 25, 2026
The backfill job requests 200 reviews per restaurant (to capture March 25 through today)
for any restaurant missing pre-June review history
After backfill completes switch back to the standard 70 reviews per run
Log clearly: "Backfill complete — {N} restaurants now have history from March 25 2026"

Update /scrapers/signals/outscraper_quota.py to:

Treat the backfill run as a separate quota bucket — do not count backfill records
against the monthly 15,000 cap since this is a one-time catch-up
Add a backfill_records_used field to the monthly summary

Update /db/migrations/ with a new migration to update the cap value if it is stored in the database.
3. Create /scrapers/signals/test_outscraper_updated.py that:

Confirms _MAX_REVIEWS is set to 70
Confirms monthly cap is 15,000
Confirms projected monthly usage is ~14,980 records at ~$44.94/month
Checks whether any restaurants still need the March 25 backfill
Prints a backfill status summary

Test inside Docker:
docker-compose exec scrapers python signals/test_outscraper_updated.py
Success condition:

Monthly cap confirmed at 15,000
_MAX_REVIEWS confirmed at 70
Backfill job triggered on scheduler startup for restaurants missing March 25+ history
Demo UI footer shows updated cap and projected cost (~$44.94/month)