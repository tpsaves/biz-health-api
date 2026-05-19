CREATE TABLE IF NOT EXISTS outscraper_usage (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id   uuid        REFERENCES restaurants(id),
  records_fetched int         NOT NULL,
  month           varchar(7)  NOT NULL,   -- YYYY-MM
  scraped_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outscraper_usage_month_idx ON outscraper_usage(month);

-- Backfill from existing outscraper_reviews raw_signals so historical usage is tracked.
INSERT INTO outscraper_usage (restaurant_id, records_fetched, month, scraped_at)
SELECT
  restaurant_id,
  (payload->>'total_reviews_fetched')::int,
  to_char(scraped_at AT TIME ZONE 'UTC', 'YYYY-MM'),
  scraped_at
FROM raw_signals
WHERE source = 'outscraper_reviews'
  AND payload->>'total_reviews_fetched' IS NOT NULL
ON CONFLICT DO NOTHING;
