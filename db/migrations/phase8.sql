-- Phase 8 migration: financial risk signals (SBA loans + property tax)
-- Run against an existing bizhealth database:
--   docker-compose exec db psql -U admin -d bizhealth -f /migrations/phase8.sql
--
-- All columns use IF NOT EXISTS so this script is idempotent (safe to re-run).

ALTER TABLE health_scores
    ADD COLUMN IF NOT EXISTS financial_risk_score   INT,
    ADD COLUMN IF NOT EXISTS sba_default            BOOLEAN,
    ADD COLUMN IF NOT EXISTS repeated_sba_borrowing BOOLEAN,
    ADD COLUMN IF NOT EXISTS tax_delinquent         BOOLEAN,
    ADD COLUMN IF NOT EXISTS sba_loan_count         INT,
    ADD COLUMN IF NOT EXISTS sba_latest_status      VARCHAR,
    ADD COLUMN IF NOT EXISTS sba_latest_amount      DECIMAL,
    ADD COLUMN IF NOT EXISTS tax_delinquency_years  INT;
