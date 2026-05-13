Read CLAUDE.md for full project context before doing anything.
Enhance the review velocity and rating trend signals with deeper analysis and seasonality adjustment to provide more granular and accurate score explanations. This is Phase 6b.
1. Update /scrapers/signals/google_places.py to extract and store additional fields:

Request maximum available reviews from the Google Places API to capture up to 12 months of review history
Extract monthly review counts for the last 12 months from review timestamps
Days since most recent review
Owner response rate (responses / total reviews in last 90 days)
1-star review percentage in last 60 days vs lifetime 1-star percentage
Average star rating for last 60 days vs prior 60 days
All new fields stored in the existing raw_signals JSONB payload under a velocity_metrics key

2. Create /scrapers/scoring/seasonality.py that:

Defines DFW restaurant industry seasonal adjustment factors by month:
pythonSEASONAL_FACTORS = {
    1: 0.80,   # January   -20%
    2: 0.90,   # February  -10%
    3: 1.05,   # March      +5%
    4: 1.10,   # April     +10%
    5: 1.10,   # May       +10%
    6: 1.05,   # June       +5%
    7: 0.95,   # July       -5%
    8: 0.90,   # August    -10%
    9: 1.05,   # September  +5%
    10: 1.15,  # October   +15%
    11: 1.10,  # November  +10%
    12: 1.15,  # December  +15%
}

Provides a normalize_volume(count, month) function that divides raw review count by the seasonal factor to produce a seasonally adjusted volume
Provides a compare_periods(current_period, prior_period) function that:

Normalizes both periods using their respective monthly factors before comparing
Returns percentage change after seasonal adjustment
Returns a seasonally_adjusted: true flag in the output


Provides a year_over_year(month_data, target_month) function that compares the same month this year vs last year when 12 months of data is available — falls back to seasonally adjusted period comparison when insufficient history exists

3. Update /scrapers/scoring/engine.py to compute enhanced velocity and rating metrics using seasonality adjustment:
For review_velocity_score add these factors:

monthly_volume_trend — use seasonality.year_over_year() if 12 months of data available, otherwise use seasonality.compare_periods() for 3-month comparison. Apply seasonal normalization before scoring:

Seasonally adjusted up more than 20%: +10 points
Seasonally adjusted flat within 20%: no change
Seasonally adjusted down 20-50%: -15 points
Seasonally adjusted down more than 50%: -25 points


recency_gap — days since last review:

Under 7 days: +5 points
7-30 days: no change
30-45 days: -10 points
Over 45 days: -20 points, flag as review_gap_alert: true


one_star_spike — if 1-star reviews exceed 30% of reviews in last 60 days:

Flag as one_star_spike: true
Apply -15 point penalty


owner_response_rate:

Above 50%: +5 points
20-50%: no change
Below 20%: -5 points



For rating_trend_score add these factors:

ninety_day_slope — compare average rating of last 60 days vs prior 60 days using seasonally adjusted review volumes to weight the averages:

Improving (up 0.2+ stars): +10 points, trend = 'improving'
Flat (within 0.2 stars): no change, trend = 'stable'
Declining (down 0.2-0.5 stars): -15 points, trend = 'declining'
Sharply declining (down 0.5+ stars): -25 points, trend = 'sharp_decline'


recent_vs_lifetime_gap — compare last 60 days average rating vs all-time rating:

Gap of 0.5+ stars below lifetime: -10 points, flag as rating_deterioration: true
Gap of 0.5+ stars above lifetime: +5 points


cross_source_divergence — compare Google rating vs Foursquare rating normalized to 0-5 scale:

Divergence under 0.5 stars: no change
Divergence 0.5-1.0 stars: -5 points, flag as source_divergence: true
Divergence over 1.0 stars: -10 points, flag as source_divergence: true


review_count_confidence — weight rating score by review volume:

Under 50 reviews: apply 0.7x confidence multiplier
50-200 reviews: apply 0.85x confidence multiplier
Over 200 reviews: apply 1.0x confidence multiplier



4. Update health_scores table with new columns:
sqlreview_gap_alert          boolean,
one_star_spike            boolean,
rating_deterioration      boolean,
source_divergence         boolean,
ninety_day_slope          varchar,  -- improving, stable, declining, sharp_decline
days_since_last_review    int,
owner_response_rate       int,      -- percentage 0-100
monthly_volume_trend      varchar,  -- growing, stable, declining, sharply_declining
review_count_confidence   varchar,  -- high, medium, low
seasonality_adjusted      boolean,  -- true if seasonal normalization was applied
comparison_method         varchar,  -- year_over_year or period_comparison
Create an EF Core migration for all new columns.
5. Update the score factors output to include seasonality context in each volume-related factor:
json{
  "signal": "google_places",
  "label": "Monthly review trend",
  "value": "Down 45% vs prior 3 months (seasonally adjusted: down 28%)",
  "impact": "negative",
  "weight": "high",
  "flag": "review_gap_alert",
  "seasonallyAdjusted": true,
  "comparisonMethod": "period_comparison"
}
6. Update /demo/index.html to display the new factors in the Level 2 signal breakdown:

Show monthly review trend with a small sparkline bar chart (last 12 months if available, last 6 months otherwise)
Add a seasonal adjustment note below the sparkline: "Volumes adjusted for DFW seasonal patterns"
Show days since last review with color indicator (green under 7, yellow 7-30, red over 30)
Show 90-day rating slope with arrow indicator
Show cross-source divergence flag if triggered
Show owner response rate as a percentage
Show comparison method used (Year-over-year or Period comparison) so lenders understand the basis
Highlight any active flags (review_gap_alert, one_star_spike, rating_deterioration, source_divergence) as warning badges at the top of the score card

7. Create /scrapers/scoring/test_engine_v4.py that:

Runs the enhanced scoring engine against all 5 onboarded DFW restaurants
Prints a full breakdown of all new factors for each restaurant
Prints which comparison method was used for each restaurant (year-over-year vs period)
Confirms all new columns populated in health_scores

Add comments explaining any Python-specific patterns that differ from C#.
Test inside Docker:
docker-compose exec scrapers python scoring/test_engine_v4.py
Then open /demo/index.html and search for Pecan Lodge to verify all new factors appear in the Level 2 drill-down with seasonal adjustment notes.
Success condition: score factors show seasonally adjusted volume trends, sparkline renders 12 months of history where available, warning badges appear for triggered flags, and comparison method is visible in the UI.