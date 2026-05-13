Read CLAUDE.md for full project context before doing anything.
This is a targeted fix to the scoring engine from Phase 6b. One issue needs to be addressed before the demo UI is reliable.
The problem
monthly_volume_trend is showing sharply_declining on well-established healthy restaurants because the Google Places API only returns 5 reviews. With so few data points the period comparison is statistically unreliable and produces misleading negative signals.
The fix
1. Update /scrapers/scoring/engine.py to add a confidence gate to monthly_volume_trend:

Count the total number of review data points available across both comparison periods
If total data points are fewer than 10, set monthly_volume_trend to insufficient_data and apply zero scoring adjustment — no penalty, no bonus
Only apply scoring adjustments (penalties or bonuses) when 10 or more data points are available
Add a new field volume_trend_confidence to the score factors output:
json{
  "signal": "google_places",
  "label": "Monthly review trend",
  "value": "Insufficient data (5 reviews available, 10 required)",
  "impact": "neutral",
  "weight": "high",
  "seasonallyAdjusted": false,
  "comparisonMethod": "insufficient_data"
}

Apply the same confidence gate logic to ninety_day_slope for consistency — already returns insufficient_data but make sure zero penalty is confirmed
Apply the same confidence gate to recent_vs_lifetime_gap — require minimum 10 reviews in the recent period before applying any rating gap penalty
Apply the same confidence gate to owner_response_rate — require minimum 10 reviews before computing response rate

2. Update health_scores table to add:
sqlvolume_trend_confidence   varchar,  -- sufficient, insufficient_data
Create an EF Core migration for the new column.
3. Update /demo/index.html to handle insufficient_data gracefully:

When monthly_volume_trend is insufficient_data show a gray neutral indicator instead of a red declining arrow
Add a tooltip or note explaining: "Trend data will improve as review history accumulates"
Never show a red warning badge for insufficient_data flags — only show warning badges for confirmed negative signals with sufficient data

4. Update /scrapers/scoring/test_engine_v4.py to re-run against all 5 restaurants and confirm:

monthly_volume_trend shows insufficient_data on restaurants with fewer than 10 review data points
No scoring penalties applied for insufficient data
sharply_declining no longer appears on healthy well-established restaurants

Test inside Docker:
docker-compose exec scrapers python scoring/test_engine_v4.py
Success condition: no healthy restaurant shows sharply_declining or a red trend warning due to insufficient data. All 5 restaurants show neutral gray indicators for volume trend with the explanatory tooltip.