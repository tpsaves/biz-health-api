# Phase 8 — SBA Loan History and Business Personal Property Tax Delinquency

Read CLAUDE.md for full project context before doing anything.

Add two new financial distress signals: SBA loan history and business personal property tax delinquency. This is Phase 8.

## 1. Create `/scrapers/signals/sba_loans.py`

Accepts a restaurant name and zip code as input.

Queries the SBA 7(a) and 504 loan dataset on Data.gov:
`https://data.sba.gov/api/3/action/datastore_search`

Uses fuzzy name matching (fuzz.token_sort_ratio from the `thefuzz` library) with a minimum score of 80 to match restaurant name against `BorrowerName` field.

Extracts for each matched loan:
- `LoanStatus` — active, paid in full, charged off (defaulted), cancelled
- `GrossApproval` — original loan amount
- `ApprovalDate` — when the loan was approved
- `InitialInterestRate`
- `TermInMonths`
- `ChargeOffDate` — if applicable, date the loan was written off as a loss

Writes raw matched loan records as JSONB to `raw_signals` with source set to `sba_loans`.

Flags as high risk if any loan shows `LoanStatus` of `CHGOFF` (charged off / defaulted).
Flags as moderate risk if restaurant has 2 or more SBA loans in history (repeated borrowing).
No API key required — SBA Data.gov is a free public dataset.

Create `/scrapers/signals/test_sba_loans.py` that:
- Tests against all onboarded DFW restaurants
- Tests against 2 known restaurant chains that likely have SBA loan history
- Prints match results including fuzzy match score, loan status, and amount for each
- Confirms data landed in `raw_signals`

## 2. Create `/scrapers/signals/property_tax.py`

Accepts a restaurant name and city as input.

Queries the appropriate county appraisal district based on city:
- Dallas, Irving, Garland → Dallas Central Appraisal District: dallascad.org
- Fort Worth, Arlington, Grand Prairie → Tarrant Appraisal District: tad.org
- Plano, Frisco, McKinney → Collin CAD: collincad.org
- Denton → Denton CAD: dentoncad.com

Searches for business personal property accounts matching the restaurant name using fuzzy matching (minimum score 80).

Extracts:
- Account status (current, delinquent)
- Years delinquent if applicable
- Total delinquent amount if available
- Assessed value of business personal property

Writes raw records as JSONB to `raw_signals` with source set to `property_tax`.

Flags as high risk if delinquent for 2+ years.
Flags as moderate risk if delinquent for 1 year.
Handles gracefully if CAD portal does not expose a public API — log as `no_data_available`.

Create `/scrapers/signals/test_property_tax.py` that:
- Tests against all onboarded DFW restaurants
- Prints match results including fuzzy match score, account status, and delinquency details
- Logs `no_data_available` clearly for any CAD without a usable API

## 3. Update `/scrapers/scoring/engine.py`

Add a new `financial_risk_score` component (0-100):
- Start at 100 (no risk)
- SBA loan charged off / defaulted: -40 points, flag `sba_default: true`
- SBA loan active: -10 points
- Multiple SBA loans (2+): -15 points, flag `repeated_sba_borrowing: true`
- Property tax delinquent 1 year: -20 points, flag `tax_delinquent: true`
- Property tax delinquent 2+ years: -35 points, flag `tax_delinquent: true`
- No SBA or tax data found: score stays at 100, no flags

Update `overall_score` weights:
- review_velocity_score: 20%
- rating_trend_score: 30%
- operational_score: 30%
- financial_risk_score: 20%

Add score caps:
- `sba_default: true` caps `overall_score` at 45
- `tax_delinquent: true` for 2+ years caps `overall_score` at 55

## 4. Update `health_scores` table

Add new columns:
```sql
financial_risk_score      int,
sba_default               boolean,
repeated_sba_borrowing    boolean,
tax_delinquent            boolean,
sba_loan_count            int,
sba_latest_status         varchar,   -- active, paid_in_full, charged_off, none_found
sba_latest_amount         decimal,
tax_delinquency_years     int,
```

Create an EF Core migration for all new columns.

## 5. Update score factors output

Include new financial signals as individual factors:
```json
{
  "signal": "sba_loans",
  "label": "SBA loan history",
  "value": "1 active loan — $250,000 approved 2023",
  "impact": "negative",
  "weight": "medium",
  "flag": null
},
{
  "signal": "property_tax",
  "label": "Business property tax",
  "value": "Current — no delinquency",
  "impact": "positive",
  "weight": "medium",
  "flag": null
}
```

## 6. Update `/demo/index.html`

Display financial risk signals in Level 2 drill-down:
- Show SBA loan history with status badge (green: none/paid, yellow: active, red: defaulted)
- Show property tax status with delinquency years if applicable
- Show `financial_risk_score` as a fourth component score bar
- Add `sba_default` and `tax_delinquent` to warning badges section

## 7. Add `thefuzz` to `/scrapers/requirements.txt`

## 8. Update `/scrapers/scheduler.py`

Add:
- SBA loans scraper: weekly Sunday 1:30 AM UTC
- Property tax scraper: weekly Sunday 2:00 AM UTC

## 9. Create `/scrapers/scoring/test_engine_v6.py`

- Runs full scoring engine against all restaurants
- Prints all four component scores including `financial_risk_score`
- Prints any active financial risk flags
- Confirms new columns populated in `health_scores`

Add comments explaining Python fuzzy matching patterns vs string comparison in C#.

## Test Commands

```
docker-compose exec scrapers python signals/test_sba_loans.py
docker-compose exec scrapers python signals/test_property_tax.py
docker-compose exec scrapers python scoring/test_engine_v6.py
```

## Success Condition

- SBA loan scraper returns results or `none_found` for all restaurants without crashing
- Property tax scraper returns results or `no_data_available` without crashing
- `financial_risk_score` appears in health_scores for all restaurants
- Demo UI shows fourth component score bar for financial risk
- Warning badges appear for any triggered financial risk flags
- 25/25 restaurants pass scoring with updated weight distribution

## Result

25/25 passed. Weight distribution confirmed:
- review_velocity_score: 20%
- rating_trend_score: 30%
- operational_score: 30%
- financial_risk_score: 20%

Score caps confirmed:
- sba_default=True → overall_score capped at 45
- tax_delinquent 2+ years → overall_score capped at 55

Pecan Lodge result: overall=88, financial_risk=100, operational=95
