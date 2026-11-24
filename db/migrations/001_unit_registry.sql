-- 001: unit registry columns on sensor_unit.
--
-- The five demo units' safe limits live in analytics/config.py, next to the measurements
-- that justify them. A unit installed later - the live probe in a real fridge - cannot be
-- added by editing code on site, so its limits are stored here and registered through the
-- admin API. analytics/registry.py merges the two.
--
-- Idempotent: safe to run on a database that already has these columns.
-- Apply to a running database with:
--   docker exec -i coldsense-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/migrations/001_unit_registry.sql

ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS monitored     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS setpoint_c    DOUBLE PRECISION;
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS max_safe_c    DOUBLE PRECISION;
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS min_safe_c    DOUBLE PRECISION;
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS categories    TEXT[] NOT NULL DEFAULT '{}';
-- 'data': a replayed historical record, judged against its own newest reading.
-- 'wall': a live device, judged against the real clock.
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS clock         TEXT NOT NULL DEFAULT 'data';
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS warmup_min    INTEGER NOT NULL DEFAULT 30;
ALTER TABLE sensor_unit ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sensor_unit_clock_check') THEN
        ALTER TABLE sensor_unit ADD CONSTRAINT sensor_unit_clock_check CHECK (clock IN ('data', 'wall'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sensor_unit_limits_check') THEN
        -- a monitored unit must have a band, and the band must contain its setpoint
        ALTER TABLE sensor_unit ADD CONSTRAINT sensor_unit_limits_check CHECK (
            NOT monitored OR (
                setpoint_c IS NOT NULL AND max_safe_c IS NOT NULL AND max_safe_c > setpoint_c
                AND (min_safe_c IS NULL OR min_safe_c < setpoint_c)
            )
        );
    END IF;
END $$;
