-- Adds is_backfill flag to outscraper_usage so one-time catch-up records
-- are tracked separately and excluded from the monthly 15,000-record cap.
ALTER TABLE outscraper_usage
    ADD COLUMN IF NOT EXISTS is_backfill boolean NOT NULL DEFAULT false;
