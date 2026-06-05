-- Phase 14: businessStatus from Google Places + hours-per-day analysis
ALTER TABLE health_scores
    ADD COLUMN IF NOT EXISTS business_status     varchar,
    ADD COLUMN IF NOT EXISTS temporarily_closed  boolean,
    ADD COLUMN IF NOT EXISTS permanently_closed  boolean,
    ADD COLUMN IF NOT EXISTS total_weekly_hours  decimal,
    ADD COLUMN IF NOT EXISTS hours_reduction_pct decimal,
    ADD COLUMN IF NOT EXISTS hours_reduction     boolean;
