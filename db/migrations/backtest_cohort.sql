CREATE TABLE IF NOT EXISTS backtest_cohort (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id         uuid REFERENCES restaurants(id),
  cohort_type           varchar,    -- retrospective, prospective
  baseline_score        int,
  baseline_risk_band    varchar,    -- low, moderate, elevated, high
  baseline_date         date,
  baseline_factors      JSONB,      -- full score breakdown at baseline
  outcome_90d           varchar,    -- open, reduced_hours, closed_temporarily, closed_permanently, unknown
  outcome_180d          varchar,
  outcome_90d_date      date,
  outcome_180d_date     date,
  closure_date          date,
  closure_source        varchar,
  notes                 text,
  created_at            timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_backtest_cohort_restaurant_id  ON backtest_cohort(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_backtest_cohort_cohort_type    ON backtest_cohort(cohort_type);
CREATE INDEX IF NOT EXISTS idx_backtest_cohort_baseline_date  ON backtest_cohort(baseline_date);
CREATE INDEX IF NOT EXISTS idx_backtest_cohort_risk_band      ON backtest_cohort(baseline_risk_band);
