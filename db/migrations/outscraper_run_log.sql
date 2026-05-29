CREATE TABLE IF NOT EXISTS outscraper_run_log (
  id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_date              date        NOT NULL DEFAULT CURRENT_DATE,
  status                varchar(16) NOT NULL,   -- ok | throttled | skipped
  restaurants_completed int         NOT NULL DEFAULT 0,
  restaurants_skipped   int         NOT NULL DEFAULT 0,
  records_used_before   int         NOT NULL DEFAULT 0,
  records_used_after    int         NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outscraper_run_log_created_at_idx ON outscraper_run_log(created_at DESC);
