CREATE TABLE IF NOT EXISTS restaurants (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    zip             TEXT,
    google_place_id TEXT        UNIQUE,
    yelp_id         TEXT        UNIQUE,
    website         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_signals (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    source        TEXT        NOT NULL,
    payload       JSONB       NOT NULL,
    scraped_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health_scores (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id         UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    review_velocity_score INT,
    rating_trend_score    INT,
    operational_score     INT,
    staffing_score        INT,
    overall_score         INT,
    score_factors         JSONB,
    scored_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Phase 6b: enhanced velocity and rating fields
    review_gap_alert          BOOLEAN,
    one_star_spike            BOOLEAN,
    rating_deterioration      BOOLEAN,
    source_divergence         BOOLEAN,
    ninety_day_slope          VARCHAR,
    days_since_last_review    INT,
    owner_response_rate       INT,
    monthly_volume_trend      VARCHAR,
    review_count_confidence   VARCHAR,
    seasonality_adjusted      BOOLEAN,
    comparison_method         VARCHAR,

    -- Phase 8: financial risk signals (SBA loans + property tax)
    financial_risk_score      INT,
    sba_default               BOOLEAN,
    repeated_sba_borrowing    BOOLEAN,
    tax_delinquent            BOOLEAN,
    sba_loan_count            INT,
    sba_latest_status         VARCHAR,
    sba_latest_amount         DECIMAL,
    tax_delinquency_years     INT
);

CREATE INDEX IF NOT EXISTS idx_raw_signals_restaurant_id  ON raw_signals(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_raw_signals_scraped_at     ON raw_signals(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_health_scores_restaurant_id ON health_scores(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_health_scores_scored_at    ON health_scores(scored_at DESC);
