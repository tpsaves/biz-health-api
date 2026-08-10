Read CLAUDE.md for full project context before doing anything.
Two critical improvements to the backtesting framework: fix train/test leakage in the retrospective backtest, and build a lead-time early-warning exhibit. This is Phase 13.

Part 1 — Fix Train/Test Leakage
The problem
The composite risk cap (operational < 65 AND velocity < 30 caps overall at 59) was tuned on the same 9 retrospective restaurants we then reported 100% recall on. This is data leakage — a model-risk reviewer will reject any performance number computed on data the model was tuned on. We need a clean train/test split.
Create /scrapers/backtesting/holdout_validation.py that:

Splits the closed_restaurants dataset into train and test sets:

Train set: closed restaurants with closure_date BEFORE a cutoff date (e.g. closures in 2024)
Test set (out-of-time): closed restaurants with closure_date AFTER the cutoff (e.g. closures in 2025+)
This is an OUT-OF-TIME split — the test set is strictly later than the train set, mirroring how the model would actually be used


Any scoring rules, thresholds, or caps may only be derived from the train set
Report performance metrics computed ONLY on the test set the model never saw
Print:

Train set size and date range
Test set size and date range
Precision, recall, and F1 on the test set only
Confirmation that no test-set restaurant was used to derive any threshold



Create a leakage audit in the same file that:

Lists every threshold and cap currently in engine_v2.py
For each one documents what data it was derived from
Flags any threshold that was tuned on restaurants appearing in the test set
Prints a clear PASS/FAIL on leakage for each rule

Important: Report the honest test-set performance even if it is lower than 100%. A realistic out-of-time number that survives scrutiny is the goal. Do not tune anything to make the test number higher — that would re-introduce leakage.

Part 2 — Lead-Time Early-Warning Exhibit
Create /scrapers/backtesting/lead_time_analysis.py that:

For each closed restaurant in closed_restaurants with sufficient historical signal data:

Reconstruct the overall_score at multiple points before closure: T-30, T-60, T-90, T-120, T-150, T-180 days
Identify the first point at which the score dropped below each risk threshold (80, 60, 40)
Calculate lead time: how many days before closure did the score first signal elevated or high risk


Aggregate across all closed restaurants:

Average lead time before closure that the score first dropped below 60 (moderate)
Average lead time before closure that the score first dropped below 40 (high)
Percentage of closures that were flagged at least 30 / 60 / 90 days in advance


Produce a summary table:
Threshold crossed | Avg lead time | % flagged 30d+ early | % flagged 90d+ early
Score below 60    | X days        | X%                   | X%
Score below 40    | X days        | X%                   | X%


Create /scrapers/backtesting/test_lead_time.py that:

Runs the lead-time analysis against all closed restaurants with reconstructable history
Prints the summary table
Prints individual lead-time examples for 5 restaurants


Part 3 — Score-Band to Outcome-Rate Table
Create /scrapers/backtesting/band_outcome_table.py that:

Groups all backtested restaurants (retrospective + any resolved prospective) into score bands
For each band computes the closure rate (clearly labeled as closure, not payment default)
Produces the classic monotonic bad-rate table:
Score Band | Count | Closure Rate | Cumulative % of Closures Captured
80-100     | X     | X%           | X%
60-79      | X     | X%           | X%
40-59      | X     | X%           | X%
0-39       | X     | X%           | X%

Label the outcome explicitly as "closure rate (proxy outcome)" with a note that
this will be re-run against payment/write-off outcomes once design partner data is available
Flag whether the closure rate is monotonic across bands (it should increase as score worsens)


Part 4 — API and Demo UI
Add API endpoints:

GET /api/v1/backtesting/holdout — out-of-time test set results
GET /api/v1/backtesting/lead-time — early warning lead time summary
GET /api/v1/backtesting/band-table — score band to outcome rate table

Update the Backtesting tab in /demo/index.html to add:

A clearly labeled "Out-of-Time Validation" section showing test-set precision/recall
with a note: "Performance measured on closures the model was never tuned on"
A "Early Warning Lead Time" section with the lead-time table and a plain English line:
"On average our score flagged distressed restaurants X days before closure"
The score-band outcome table with the proxy-outcome label clearly visible


Part 5 — Honesty Documentation
Create /backtesting_results/methodology_notes.md documenting:

The out-of-time train/test split methodology
The reject-inference caveat: prospective cohort is restaurants we chose to score,
not a distributor's actual credit-decision population
The proxy-outcome caveat: closure is a proxy for payment default, which is the
real outcome a distributor cares about — to be validated with partner receivables data
Known limitations stated plainly


Test Sequence
docker-compose exec scrapers python backtesting/holdout_validation.py
docker-compose exec scrapers python backtesting/test_lead_time.py
docker-compose exec scrapers python backtesting/band_outcome_table.py
Success Condition

Out-of-time test set performance reported honestly (likely below 100%, that's correct)
Leakage audit shows PASS for all rules or clearly flags which need retuning on train-only data
Lead-time analysis produces average days of advance warning before closure
Score-band table is monotonic and clearly labeled as proxy outcome
methodology_notes.md documents all caveats honestly
Demo UI backtesting tab shows out-of-time results, lead time, and band table