CREATE TABLE IF NOT EXISTS closed_restaurants (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  varchar NOT NULL,
  address               varchar,
  city                  varchar,
  zip                   varchar,
  google_place_id       varchar,
  yelp_id               varchar,
  closure_date          date,
  closure_date_estimated boolean,  -- true if derived from last review date rather than a known record
  closure_source        varchar,   -- google, tabc, yelp, inspection_inference
  created_at            timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_closed_restaurants_city         ON closed_restaurants(city);
CREATE INDEX IF NOT EXISTS idx_closed_restaurants_closure_date ON closed_restaurants(closure_date);
CREATE INDEX IF NOT EXISTS idx_closed_restaurants_google_id    ON closed_restaurants(google_place_id);
