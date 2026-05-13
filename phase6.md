Read CLAUDE.md for full project context before doing anything.
Build a demo UI with full score factor drill-down. This is Phase 6.
1. Update /scrapers/scoring/engine.py to emit score factors alongside scores:

For each component score, generate a score_factors JSON array documenting every signal that contributed to the score
Each factor includes: signal source, label, value, date, impact (positive/neutral/negative), and weight (high/medium/low)
Store score_factors as JSONB in a new score_factors column on the health_scores table
Create an EF Core migration for the new column
Example factor structure:
json{
  "signal": "health_inspection",
  "label": "Latest inspection score",
  "value": "94/100",
  "date": "2026-02-10",
  "impact": "positive",
  "weight": "high"
}


2. Update the score API response to include score factors grouped by component:
json{
  "overallScore": 93,
  "operationalScore": 95,
  "scoreFactors": {
    "operational": [...],
    "reviewVelocity": [...],
    "ratingTrend": [...]
  }
}
3. Create /demo/index.html as a single self-contained HTML file that:

Has a clean, professional design suitable for showing to lenders and distributors
Includes a search form with three fields:

Restaurant Name (required)
Address or Zip (required)
City (optional dropdown defaulting to Dallas, with options: Dallas, Fort Worth, Arlington, Plano, Frisco, McKinney, Denton, Irving, Garland, Grand Prairie)


Shows a loading state while the pipeline runs
If the search returns multiple possible matches, displays a disambiguation list letting the user pick the correct restaurant before scoring
Works without any frontend build tools — pure HTML, CSS, and vanilla JavaScript
Uses the .NET API at http://localhost:8080 as its backend

4. Score display should include three levels:
Level 1 — Score Summary (always visible):

Large overall score with color indicator:

80-100: green (low risk)
60-79: yellow (moderate risk)
40-59: orange (elevated risk)
0-39: red (high risk)


Component score bars for Review Velocity, Rating Trend, Operational Health
Plain English risk recommendation:

80-100: "This restaurant shows strong signals across all categories. Suitable for standard credit terms."
60-79: "This restaurant shows mixed signals. Consider shorter net terms or additional verification."
40-59: "This restaurant shows concerning signals. Recommend reduced credit limit or collateral."
0-39: "This restaurant shows significant distress signals. Credit extension not recommended."



Level 2 — Signal Breakdown (expandable, click any component score bar to expand):

For each component score show the individual score factors that drove it
Each factor displayed as a row with: label, value, date, and a colored impact indicator (green arrow up / red arrow down / gray dash)
Example for Operational Health expanded:
✓ Latest inspection score    94/100      2026-02-10   ↑
✓ Inspection trend           Stable                   →
✓ TABC license status        Active                   ↑
✓ License expiry             2027-03-15               ↑
✓ Hours consistency          7/7 days                 ↑
⚠ License history risk       None in 90 days          →


Level 3 — Raw Evidence (expandable, show source link or raw data):

For inspection data: show all inspection records as a small table (date, score, violation count)
For TABC license: show license number, entity name, license type, status, expiry
For Google/Foursquare: show rating, review count, last scraped date

5. Add a recently scored section below the search form showing the last 5 restaurants scored with name, address, overall score, and color indicator
6. Add a compare feature that:

Allows the user to pin up to 2 restaurants side by side
Shows Level 1 and Level 2 breakdowns for both in columns
Highlights differences between the two restaurants (e.g. one has declining inspection trend, the other stable)
Shows plain English recommendation for each

7. Update POST /api/v1/restaurants/search-and-score to:

Accept name, address, and city as JSON body
Use name plus address as the primary lookup key
Return disambiguation list if multiple matches found
Cache results for 24 hours, trigger fresh run if stale
Time out at 30 seconds with partial result fallback

The UI should look professional enough to show to a Sysco credit manager or equipment lender. Clean fonts, good spacing, color-coded risk indicators, no placeholder text.
Test by opening /demo/index.html in a browser while docker-compose is running and searching for:

Torchy's Tacos, 1407 N Greenville Ave, Dallas
Pecan Lodge, 2702 Main St, Dallas

Success condition: both searches return full visual score breakdowns with all three levels working, compare feature shows them side by side with differences highlighted, score factors show the exact data points behind every component score.