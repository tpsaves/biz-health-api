Read CLAUDE.md for full project context before doing anything.
Pecan Lodge monthly breakdown is still missing March, April, and May despite having 209 reviews per Outscraper run. Need to diagnose exactly what review dates are in the raw payload.
Step 1 — Inspect the actual review dates in the Outscraper payload
Run this query:
sqlSELECT
  r->>'review_datetime_utc' as review_date,
  r->>'review_rating' as rating
FROM raw_signals rs,
jsonb_array_elements(rs.payload->'reviews_data') as r
WHERE rs.source = 'outscraper_reviews'
AND rs.restaurant_id = 'b5cca1db-6810-4b88-9a87-01f7079ecf96'
AND rs.scraped_at = (
  SELECT MAX(scraped_at) FROM raw_signals
  WHERE source = 'outscraper_reviews'
  AND restaurant_id = 'b5cca1db-6810-4b88-9a87-01f7079ecf96'
)
ORDER BY review_date DESC
LIMIT 20;
Print all 20 rows. This shows the actual dates of reviews Outscraper returned.
Step 2 — Check the monthly_breakdown field directly
sqlSELECT payload->'monthly_breakdown' as breakdown
FROM raw_signals
WHERE source = 'outscraper_reviews'
AND restaurant_id = 'b5cca1db-6810-4b88-9a87-01f7079ecf96'
ORDER BY scraped_at DESC
LIMIT 1;
Step 3 — Identify the gap
Compare the most recent review dates from Step 1 against what monthly_breakdown
shows in Step 2. Specifically:

Are there reviews dated March, April, or May 2026 in reviews_data?
If yes — why are they missing from monthly_breakdown?
If no — why is Outscraper not returning recent reviews despite sort=newest?

Step 4 — Fix based on findings

If reviews exist in reviews_data but not monthly_breakdown: fix the bucketing logic
If reviews don't exist in reviews_data: fix the Outscraper request to ensure
sort=newest is being honored and recent reviews are being fetched
If date parsing is wrong: fix the datetime parser to handle the actual format
returned by Outscraper

Step 5 — After fixing reprocess all outscraper_reviews raw_signals rows
Rebuild monthly_breakdown for all restaurants from existing payload data
without making new API calls. Print before/after for Pecan Lodge.
Success condition: Pecan Lodge monthly_breakdown shows non-zero counts for
March, April, and May 2026.