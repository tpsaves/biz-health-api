-- Phase 10: composite risk cap flag on health_scores
ALTER TABLE health_scores ADD COLUMN IF NOT EXISTS composite_risk_cap BOOLEAN DEFAULT FALSE;
