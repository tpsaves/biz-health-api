-- Signal confidence columns for health_scores
-- Distinguishes "no record found because not applicable" from
-- "no record found but one was expected" (the latter is a risk signal).

ALTER TABLE health_scores
    ADD COLUMN IF NOT EXISTS tabc_confidence              varchar,
    ADD COLUMN IF NOT EXISTS tabc_confidence_reason       varchar,
    ADD COLUMN IF NOT EXISTS inspection_confidence        varchar,
    ADD COLUMN IF NOT EXISTS inspection_confidence_reason varchar,
    ADD COLUMN IF NOT EXISTS tabc_expected_missing        boolean,
    ADD COLUMN IF NOT EXISTS inspection_expected_missing  boolean,
    ADD COLUMN IF NOT EXISTS inspection_data_unavailable  boolean;
