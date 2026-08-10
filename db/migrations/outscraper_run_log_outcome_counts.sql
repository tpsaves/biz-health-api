-- Adds two nullable outcome counters to outscraper_run_log.
-- NULL on existing rows = "predates instrumentation, not tracked".
-- New rows written by the updated log_run_status() always carry a concrete integer.
ALTER TABLE outscraper_run_log ADD COLUMN restaurants_no_reviews INTEGER NULL;
ALTER TABLE outscraper_run_log ADD COLUMN restaurants_failed     INTEGER NULL;
