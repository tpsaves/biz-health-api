Read CLAUDE.md for full project context before doing anything.
Enhance the Rating Trend component with three new signals: rating distribution shift, keyword flags in recent reviews, and response rate trend. All three use existing Outscraper review data — no new API calls needed.

1. Rating Distribution Analysis
Update /scrapers/scoring/engine_v2.py to compute rating distribution from Outscraper reviews:
For each restaurant compute:

pct_5star — percentage of reviews in last 90 days that are 5 stars
pct_1star — percentage of reviews in last 90 days that are 1 star
pct_5star_lifetime — percentage of all-time reviews that are 5 stars
pct_1star_lifetime — percentage of all-time reviews that are 1 star

Scoring adjustments to rating_trend_score:

1-star reviews > 30% of last 90 days: -15 points, flag high_negative_rate: true
1-star reviews increased 10%+ vs lifetime rate: -10 points, flag negative_rate_rising: true
5-star reviews dropped 15%+ vs lifetime rate: -10 points
Bimodal distribution (5-star > 60% AND 1-star > 20% simultaneously): -10 points,
flag bimodal_distribution: true — signals polarized customer experience
5-star reviews > 70% of last 90 days: +5 points (strong recent sentiment)


2. Keyword Flags in Recent Reviews
Create /scrapers/scoring/keyword_analyzer.py that:

Accepts a list of review texts and their timestamps
Searches only reviews from the last 60 days
Detects the following keyword categories:

pythonKEYWORD_FLAGS = {
    'operational_instability': [
        'closed early', 'not open', 'were closed', 'closed when',
        'hours wrong', 'closed randomly', 'inconsistent hours'
    ],
    'ownership_change': [
        'new owner', 'new management', 'under new ownership',
        'new staff', 'changed ownership', 'different owner'
    ],
    'sanitation_risk': [
        'health code', 'roaches', 'cockroach', 'rodent', 'mice',
        'dirty', 'filthy', 'disgusting', 'unsanitary', 'bug'
    ],
    'quality_decline': [
        'not what it used to be', 'gone downhill', 'used to be better',
        'quality dropped', 'not as good', 'disappointing',
        'went downhill', 'used to love'
    ],
    'financial_stress': [
        'prices went up', 'price increase', 'too expensive now',
        'portion smaller', 'portions smaller', 'raised prices',
        'not worth the price'
    ]
}

Returns a keyword_findings dict with:

Category name
Match count
Example phrases found (max 2 per category)
Reviews containing matches (review_id and date only — no full text stored)



Scoring adjustments to rating_trend_score:

sanitation_risk any match: -20 points, flag sanitation_flag: true
operational_instability 2+ matches: -15 points, flag operational_instability_flag: true
ownership_change any match: -10 points, flag ownership_change_flag: true
(ownership change is not always negative but warrants attention)
quality_decline 3+ matches: -10 points, flag quality_decline_flag: true
financial_stress 3+ matches: -5 points, flag financial_stress_flag: true
Multiple categories flagged simultaneously (3+): additional -10 points


3. Response Rate Trend
Update engine_v2.py to compute response rate trend over time:

Compare owner response rate in last 60 days vs prior 60 days
Uses owner_answer field from Outscraper review data
Scoring adjustments:

Response rate dropped 30%+ vs prior period: -10 points,
flag response_rate_declining: true
Was responding (>30%) and completely stopped (0% in last 60 days): -15 points,
flag owner_disengaged: true
Consistently responding (>50% both periods): +5 points


Requires minimum 5 reviews in each period — set to insufficient_data otherwise


4. Update health_scores table
Add new columns:
sql-- Rating distribution
pct_5star_recent          decimal,
pct_1star_recent          decimal,
high_negative_rate        boolean,
negative_rate_rising      boolean,
bimodal_distribution      boolean,

-- Keyword flags
sanitation_flag           boolean,
operational_instability_flag boolean,
ownership_change_flag     boolean,
quality_decline_flag      boolean,
financial_stress_flag     boolean,
keyword_findings          JSONB,

-- Response rate trend
response_rate_declining   boolean,
owner_disengaged          boolean,
response_rate_recent      int,
response_rate_prior       int,
Create EF Core migration for all new columns.

5. Update score factors output
Add new factors to the ratingTrend section of scoreFactors:
json{
  "signal": "outscraper_reviews",
  "label": "1-star review rate (last 90 days)",
  "value": "18% (vs 12% lifetime)",
  "impact": "negative",
  "weight": "high",
  "flag": "negative_rate_rising"
},
{
  "signal": "outscraper_reviews",
  "label": "Recent review keywords",
  "value": "2 mentions of ownership change, 1 quality decline",
  "impact": "negative",
  "weight": "medium",
  "flag": "ownership_change_flag"
},
{
  "signal": "outscraper_reviews",
  "label": "Owner response rate trend",
  "value": "Dropped from 45% to 8% in last 60 days",
  "impact": "negative",
  "weight": "medium",
  "flag": "owner_disengaged"
}

6. Update /demo/index.html
Add new signals to the Rating Trend Level 2 drill-down:

Rating distribution bar — small inline stacked bar showing 5-star/4-star/3-star/2-star/1-star
breakdown for last 90 days vs lifetime side by side
Keyword flags section — if any keyword flags triggered show them as colored chips:

Red chip: sanitation_flag, operational_instability_flag
Orange chip: ownership_change_flag, quality_decline_flag
Yellow chip: financial_stress_flag
Each chip shows category name and match count
Hover tooltip shows example phrases found


Owner engagement indicator — show response rate recent vs prior with trend arrow

Add new warning badges:

sanitation_flag — red badge "Sanitation Risk"
owner_disengaged — orange badge "Owner Disengaged"
bimodal_distribution — orange badge "Polarized Reviews"
ownership_change_flag — yellow badge "Ownership Change Detected"


7. Create test files
/scrapers/scoring/test_keyword_analyzer.py that:

Runs keyword analyzer against Pecan Lodge review text
Prints any keyword categories detected
Confirms no false positives on a healthy restaurant

/scrapers/scoring/test_engine_v7.py that:

Re-scores all 25 active pipeline restaurants
Prints rating_trend_score before and after for each
Prints any keyword flags, distribution flags, or response rate flags triggered
Confirms new columns populated in health_scores


Test Sequence
docker-compose exec scrapers python scoring/test_keyword_analyzer.py
docker-compose exec scrapers python scoring/test_engine_v7.py
Success Condition

Rating distribution computed for all restaurants with sufficient Outscraper data
Keyword analyzer runs without false positives on healthy restaurants
Response rate trend computed where sufficient review data exists
New warning badges appear in demo UI for any triggered flags
Pecan Lodge rating_trend_score unchanged or improved (healthy restaurant — no flags expected)
At least one restaurant in the cohort triggers a keyword or distribution flag