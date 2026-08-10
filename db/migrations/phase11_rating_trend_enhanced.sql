-- Phase 11: enhanced rating trend signals
-- Rating distribution, keyword flags, and response rate trend columns.

ALTER TABLE health_scores
  ADD COLUMN IF NOT EXISTS pct_5star_recent             decimal,
  ADD COLUMN IF NOT EXISTS pct_1star_recent             decimal,
  ADD COLUMN IF NOT EXISTS high_negative_rate           boolean,
  ADD COLUMN IF NOT EXISTS negative_rate_rising         boolean,
  ADD COLUMN IF NOT EXISTS bimodal_distribution         boolean,
  ADD COLUMN IF NOT EXISTS sanitation_flag              boolean,
  ADD COLUMN IF NOT EXISTS operational_instability_flag boolean,
  ADD COLUMN IF NOT EXISTS ownership_change_flag        boolean,
  ADD COLUMN IF NOT EXISTS quality_decline_flag         boolean,
  ADD COLUMN IF NOT EXISTS financial_stress_flag        boolean,
  ADD COLUMN IF NOT EXISTS keyword_findings             jsonb,
  ADD COLUMN IF NOT EXISTS response_rate_declining      boolean,
  ADD COLUMN IF NOT EXISTS owner_disengaged             boolean,
  ADD COLUMN IF NOT EXISTS response_rate_recent         int,
  ADD COLUMN IF NOT EXISTS response_rate_prior          int;
