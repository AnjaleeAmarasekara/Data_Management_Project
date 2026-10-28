-- ColdSense-DQ schema
-- Layers: sensor_unit (data source registry) -> sensor_reading (raw IoT stream, hypertable)
--         -> quality_score (Data Quality layer output) -> sales_transaction (retail business data)

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Registry mapping each physical sensor (device MAC id from the dataset) to a modeled
-- cold-storage unit. The source dataset has no branch/store field, so we model a single
-- branch with three refrigeration units, one per device. See docs/data_linkage.md.
CREATE TABLE sensor_unit (
    device_id    TEXT PRIMARY KEY,
    unit_name    TEXT NOT NULL,
    unit_type    TEXT NOT NULL,
    branch_name  TEXT NOT NULL DEFAULT 'Main Branch'
);

-- Raw IoT telemetry, ingested as-is (no cleaning at this stage -- that is the Data
-- Quality layer's job, not ingestion's). temp/humidity drive the cold-chain logic;
-- co/lpg/smoke/motion/light are carried through from the source dataset for provenance
-- but are not used by the data quality or analytics layers.
CREATE TABLE sensor_reading (
    id        BIGSERIAL,
    device_id TEXT NOT NULL REFERENCES sensor_unit(device_id),
    ts        TIMESTAMPTZ NOT NULL,
    temp      DOUBLE PRECISION,
    humidity  DOUBLE PRECISION,
    co        DOUBLE PRECISION,
    lpg       DOUBLE PRECISION,
    smoke     DOUBLE PRECISION,
    motion    BOOLEAN,
    light     BOOLEAN,
    PRIMARY KEY (device_id, ts, id)
);

SELECT create_hypertable('sensor_reading', 'ts');
CREATE INDEX ON sensor_reading (device_id, ts DESC);

-- Data Quality layer output: one row per (device, evaluation window). This is the
-- system's "trust score" for a slice of sensor history -- what analytics/dashboard
-- consume instead of raw readings. Mirrors the proposal's QualityScore class.
CREATE TABLE quality_score (
    id             BIGSERIAL PRIMARY KEY,
    device_id      TEXT NOT NULL REFERENCES sensor_unit(device_id),
    window_start   TIMESTAMPTZ NOT NULL,
    window_end     TIMESTAMPTZ NOT NULL,
    missing_flag   BOOLEAN NOT NULL DEFAULT FALSE,
    outlier_flag   BOOLEAN NOT NULL DEFAULT FALSE,
    drift_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    noise_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    integrity_flag BOOLEAN NOT NULL DEFAULT FALSE,
    trust_value    NUMERIC(5,2) NOT NULL,
    quality_label  TEXT NOT NULL,
    generated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON quality_score (device_id, window_start DESC);

-- Retail business data (independent source, joined to sensor data only via the
-- modeled relative_day index -- see docs/data_linkage.md for why).
CREATE TABLE sales_transaction (
    invoice_id       TEXT PRIMARY KEY,
    sale_date        DATE NOT NULL,
    relative_day     INTEGER NOT NULL,
    customer_type    TEXT,
    gender           TEXT,
    product_category TEXT NOT NULL,
    unit_price       NUMERIC(10,2) NOT NULL,
    quantity         INTEGER NOT NULL,
    total_sales      NUMERIC(10,2) NOT NULL,
    payment_method   TEXT
);

CREATE INDEX ON sales_transaction (relative_day);
CREATE INDEX ON sales_transaction (product_category);

-- Row-level output of the Data Quality layer: the specific readings that were flagged.
-- Analytics excludes these; the dashboard highlights them on the charts.
CREATE TABLE reading_flag (
    device_id TEXT NOT NULL REFERENCES sensor_unit(device_id),
    ts        TIMESTAMPTZ NOT NULL,
    reason    TEXT NOT NULL,
    PRIMARY KEY (device_id, ts, reason)
);

-- Analytics output: detected events (setpoint excursions and Isolation Forest anomalies).
CREATE TABLE anomaly_event (
    id            BIGSERIAL PRIMARY KEY,
    device_id     TEXT NOT NULL REFERENCES sensor_unit(device_id),
    kind          TEXT NOT NULL,
    start_ts      TIMESTAMPTZ NOT NULL,
    end_ts        TIMESTAMPTZ NOT NULL,
    minutes       INTEGER NOT NULL,
    peak_temp_c   DOUBLE PRECISION NOT NULL,
    severity      TEXT NOT NULL,
    avg_trust     NUMERIC(5,2)
);
CREATE INDEX ON anomaly_event (device_id, start_ts);

-- Analytics output: per unit per relative day business impact (see docs/data_linkage.md).
CREATE TABLE impact_estimate (
    device_id            TEXT NOT NULL REFERENCES sensor_unit(device_id),
    relative_day         INTEGER NOT NULL,
    categories           TEXT NOT NULL,
    sales_exposed        NUMERIC(10,2) NOT NULL,
    excursion_minutes    INTEGER NOT NULL,
    degree_minutes       DOUBLE PRECISION NOT NULL,
    peak_temp_c          DOUBLE PRECISION NOT NULL,
    spoilage_probability DOUBLE PRECISION NOT NULL,
    revenue_at_risk      NUMERIC(10,2) NOT NULL,
    avg_trust            NUMERIC(5,2) NOT NULL,
    PRIMARY KEY (device_id, relative_day)
);

-- Users and hierarchical roles. role_level lets a permission check be a single ">="
-- comparison instead of an explicit list per role, which is how the hierarchy is enforced.
CREATE TABLE app_user (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL,
    role_level    SMALLINT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Alerts a Quality Supervisor or above has acknowledged, for the RBAC demo.
CREATE TABLE alert_acknowledgement (
    anomaly_event_id BIGINT PRIMARY KEY REFERENCES anomaly_event(id),
    acknowledged_by  BIGINT NOT NULL REFERENCES app_user(id),
    acknowledged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    note             TEXT
);

-- Ingestion-layer security log: unauthorized/unknown-device attempts at the device
-- ingestion API. This is the literal "intrusion detection" piece -- monitoring who is
-- trying to push data in, distinct from judging whether accepted data is trustworthy
-- (that's reading_flag/quality_score, which covers spoofed-but-authenticated data).
CREATE TABLE security_event (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,   -- 'auth_failed' | 'unknown_device'
    device_id   TEXT,
    detail      TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON security_event (occurred_at DESC);
