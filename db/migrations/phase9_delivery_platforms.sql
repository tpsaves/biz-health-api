-- Phase 9: delivery platform listing status columns
-- Apply to existing databases that were created before Phase 9.
-- Fresh deployments use init.sql which already includes these columns.
--
-- Run via:
--   docker-compose exec db psql -U admin -d bizhealth -f /migrations/phase9_delivery_platforms.sql
-- Or from the host:
--   docker-compose exec -T db psql -U admin -d bizhealth < db/migrations/phase9_delivery_platforms.sql

ALTER TABLE health_scores
    ADD COLUMN IF NOT EXISTS doordash_listed         BOOLEAN,
    ADD COLUMN IF NOT EXISTS ubereats_listed         BOOLEAN,
    ADD COLUMN IF NOT EXISTS delivery_platform_count INT,
    ADD COLUMN IF NOT EXISTS delivery_status         VARCHAR,
    ADD COLUMN IF NOT EXISTS delivery_platform_loss  BOOLEAN;
