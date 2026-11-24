# ColdSense-DQ

Data-quality-driven cold-chain intelligence platform for retail SMEs. IoT temperature/humidity
streams and retail sales data are validated by a data quality layer *before* any analytics
touches them, so alerts and business-impact estimates rest on data that has earned trust.

Development data is real refrigeration telemetry from 123 instrumented fridges in a tropical
climate (1,264,809 readings across 861 sensor channels: 615 cold units plus a product probe
and an ambient sensor per premises) joined to real perishable retail sales.
Both stand in for confidential Sri Lankan SME data; docs/data_linkage.md records every
modelling decision and limitation.

See the project proposal for the problem statement and architecture. This README covers running
what is built (mid-review phase): ingestion, storage, the data quality framework, analytics
(anomaly detection, ARIMA forecasting, business impact), a role-gated API, and a web dashboard
with authentication, reporting and user management.

## Prerequisites

- Python 3.11+
- Docker Desktop (PostgreSQL + TimescaleDB)
- Node.js 22.12+ (the dashboard uses Angular 20)
- A Kaggle account (one of the two datasets is distributed through Kaggle)

## First-time setup

```bash
# 1. Database (db/init.sql and db/migrations/ load automatically on first start)
docker compose up -d
#    An existing database volume needs each new migration applied once (safe to re-run):
docker exec -i coldsense-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/migrations/001_unit_registry.sql

# 2. Python environment
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
cp .env.example .env
# then replace both secrets in .env -- the API refuses to start with the placeholders:
python -c "import secrets; print(secrets.token_urlsafe(48))"   # once for JWT_SECRET, once for DEVICE_API_KEY

# 3. Datasets (see docs/data_linkage.md for why these two were chosen)
#    a. Refrigerator telemetry: 123 instrumented fridges, Bangkok (CC BY-NC-SA 4.0).
#       Download the .txt source files from https://doi.org/10.57745/TMWYBQ into:
#       data/raw/bangkok_fridges/01_src/
#    b. Retail perishable sales: Kaggle "Store Sales - Time Series Forecasting".
#       Place train.csv and stores.csv into data/raw/favorita/

# 4. Data pipeline: ingest -> data quality scoring -> analytics (order matters)
cd ingestion && python load_bangkok_sensors.py && python load_favorita_sales.py && cd ..
python dq_engine/run_scoring.py
python analytics/run_analytics.py

# 5. Demo user accounts (admin/operations manager/quality supervisor/staff -- see docs/access_control.md)
python backend/seed_users.py

# 6. Dashboard dependencies
cd frontend && npm install && cd ..
```

Re-running step 4's scoring and analytics scripts is safe; they clear their own output first.
Scoring all 861 channels takes about 4 minutes with `--workers=6` (longer serially); add
`--resume` to continue an interrupted run instead of starting over; pass a site id (`python dq_engine/run_scoring.py
BKK-012`) to rescore just the demo premises in seconds while tuning.

## Running the demo

```bash
# terminal 1: API (http://localhost:8000/docs for the interactive API docs)
python -m uvicorn backend.app.main:app --port 8000

# terminal 2: dashboard (http://localhost:4200)
cd frontend && npx ng serve
```

Optional live feed (terminal 3) -- replays historical readings as a real device would,
pushed to the actual ingestion API (`POST /api/ingest/readings`, device-key authenticated,
not a user login) so the Real-Time Monitoring page moves. This is the "Sensor Data Stream"
path in the architecture diagram -- see `docs/architecture_mapping.md`.

```bash
cd ingestion
python simulate_stream.py BKK-012-MS                 # Middle Shelf Chiller, ~4 h of data at 60x
python simulate_stream.py --cleanup                  # remove the replayed rows afterwards
```

### Live probes

A live probe is a unit registered on the **Devices** page (admin). It is judged against the
real clock: it goes silent when it stops reporting, and it gets a warm-up period after it is
fitted. Two things can feed it, through the same API and the same device key:

- **The ESP32 probe**: an ESP32 with a DS18B20 in a real fridge. Firmware, wiring and setup are
  in `hardware/esp32_probe/README.md`. The API must listen on the network for it
  (`--host 0.0.0.0`).
- **The virtual probe**: a simulated fridge, with a thermostat, heat leak and a lagging probe,
  that can be told what to go wrong. Use it when there is no board, or to show a fault on demand:

```bash
python ingestion/virtual_probe.py LIVE-001-MS --backfill-h 3            # 3 h of history, then live every 5 min
python ingestion/virtual_probe.py LIVE-002-MS --scenario door --fault-at 20
python ingestion/virtual_probe.py LIVE-003-FC --scenario defrost --dry-run --backfill-h 2
# scenarios: normal, door, power, stuck-on (freezes), dies (goes silent), defrost
python ingestion/push_reading.py LIVE-001-MS 9.0                         # one hand-typed reading
```

Both report every 5 minutes, the rate the quality thresholds were calibrated at. Both hold
readings they could not deliver and send them as one batch when the API is back.

Open http://localhost:4200 and sign in with one of the seeded accounts (password
`ColdSense#2025` for all): `admin@coldsense.lk`, `ops.manager@coldsense.lk`,
`supervisor@coldsense.lk`, `staff@coldsense.lk`. What each role can do is in
`docs/access_control.md`. `docs/demo_script.md` is a suggested walkthrough for the review.

## Tests

```bash
python -m pytest dq_engine/tests analytics/tests ingestion/tests backend/tests -v
# the backend/tests marked live call the running API and skip when it is not up
```

The tests inject synthetic faults (dropout, spike, drift, noise, duplicate timestamps,
calibration bias, replay/frozen signal, physically impossible jump) into a clean window and
assert the matching check fires, and stays quiet on clean data. They also pin down two
documented blind spots (subtle calibration bias, replay detection -- see
`docs/intrusion_detection.md`).

## How well does the quality layer actually work?

```bash
python -m dq_engine.evaluation.evaluate_quality    # writes docs/ml_evaluation_quality_layer.json
```

The same fault injection, run at scale and scored as a classifier: ten fault types injected into
300 clean real windows each, against 600 real windows left untouched. Precision **0.91**, recall
**0.88**, F1 **0.89** at the check level. The Data Quality page serves these figures from the JSON
at `GET /api/quality/evaluation`, so what a reviewer reads on the dashboard is the measurement,
not a claim typed into the page.

Two details that matter when quoting the numbers:

- The 600 real windows are **not** filtered for cleanliness first. Some are genuinely faulty, so
  the false-alarm rate (8.8% raise a check flag) is an upper bound. Filtering them with the same
  checks would drive it to zero by construction and prove nothing.
- Check level and label level differ on purpose. Only 1.7% of real windows actually fall below
  High, because an isolated bad reading is excluded at row level without pulling the window down.
  Judged on the label alone, recall is 0.68: the same design decision, seen from the other side.

`analytics/evaluation/` holds the equivalents for the models: `evaluate_anomaly.py` (Isolation
Forest, budget-matched) and `evaluate_forecast.py` (walk-forward ARIMA backtest).

## Layout

```
db/init.sql         schema: sensor_unit, sensor_reading (hypertable), quality_score, reading_flag,
                    anomaly_event, impact_estimate, sales_transaction, app_user,
                    alert_acknowledgement, security_event
ingestion/          CSV loaders (batch), replay simulator, virtual_probe.py (simulated live fridge),
                    push_reading.py (one manual reading) -- all call the ingestion API
hardware/           esp32_probe/: firmware and wiring for the real live probe
dq_engine/          the data quality framework: checks/ (missing, outlier, drift, noise,
                    integrity -- tampering/spoofing signatures), scoring.py (trust score +
                    label), faults.py (fault injection), run_scoring.py
analytics/          prepare.py (quality-filtered minute series), anomaly.py (excursions +
                    Isolation Forest), forecast.py (linear trend), arima.py (ARIMA forecast,
                    falls back to forecast.py), business_impact.py, run_analytics.py
backend/app/        FastAPI: auth (login), ingest (device-authenticated sensor push --
                    the "Sensor Data Stream" path, logs unauthorized attempts), units (live),
                    quality, alerts (+ acknowledge), demo (fault injection), reports
                    (JSON + PDF), security (unauthorized-access log), admin
backend/seed_users.py   creates the demo accounts for each role
frontend/           Angular dashboard: Real-Time Monitoring, Data Quality (+ Ingestion
                    Security panel), Alerts, Risk & Business Impact, Reports, User
                    Management, plus login/auth
docs/               data_linkage.md, access_control.md (roles),
                    architecture_mapping.md (diagram -> code), intrusion_detection.md,
                    demo_script.md
```

## Design decisions worth knowing before reading the code

- **Quality before analytics, enforced in code.** `analytics/prepare.py` drops the readings the
  quality layer flagged and tags each remaining minute with its window's trust score. Analytics
  never sees raw readings.
- **The datasets are stand-ins and are linked by a modelled join.** The sensor data is real
  refrigeration telemetry, so there is no setpoint simulation; what remains modelled is the date
  anchor and the category-to-zone mapping. See `docs/data_linkage.md` for those, the LKR price
  basis, the impact heuristic and the known limitations.
- **Thresholds are calibrated against the data, not guessed.** Moving to five-minute sampling
  broke the old settings: the noise check flagged 25% of clean freezer windows (freezers
  defrost) and drift flagged 45.8%. Both are now per equipment type, measured across all 123
  refrigerators. `docs/data_linkage.md` has the before/after table.
- **Silence is never invisible.** A sensor that stops reporting is flagged on the dashboard
  (against its sibling channels, and a whole premises against the fleet), scored as missing in the
  quality record, and listed as an alert. Before this, a 6-hour outage left no missing-data flag at
  all, because windows were built only from readings that arrived. See `docs/data_linkage.md`.
- **Historical Trends** (`/trends`) aggregates the whole record per hour, six hours or day with
  TimescaleDB's `time_bucket`: temperature range, time outside the band in both directions, data
  completeness and trust, with product and ambient temperature alongside. It does not show
  time-of-day patterns, because the loggers record elapsed time, not clock time.
- **Both ends of the safe band are failures.** Chilled units carry a lower bound as well as
  an upper one: freezing ruins dairy and produce. Across the fleet, 53 of 123 top shelves sit
  at or below 0 °C for more than 5% of the time, which a warm-only system reports as normal.
- **Spoilage is estimated from product temperature where a probe exists**, not from the air
  around the goods, and every impact row records which basis it used. The two disagree for
  14 of 123 fridges. See `docs/data_linkage.md`.
- **Humidity is validated, not just analysed.** It feeds the anomaly detector's features, so
  it goes through the same outlier and noise checks as temperature, with thresholds measured
  separately because humidity moves far more.
- **One of the checks was re-enabled on evidence.** Replay detection was disabled against the
  earlier dataset, whose sensor repeated 83% of readings. These loggers repeat 7.6-27% by zone,
  so it was re-validated and shipped for every zone except the vegetable drawer. See
  `docs/intrusion_detection.md`.
- **The live trust score is the real thing.** The API scores the newest 2-hour window with the same
  `dq_engine` code as the batch job; the fault-injection demo runs the real checks on a
  corrupted copy of a real window.
- **Roles are enforced server-side, not just hidden in the UI.** Every protected endpoint checks
  the JWT and role independently of what the frontend shows; see `docs/access_control.md`.
- **Sensor data arrives through a real ingestion API, not a direct database write.** The
  architecture diagram draws "Sensor Data Stream" as distinct from retail's "Batch Upload";
  `backend/app/routers/ingest.py` is that distinct path, device-key authenticated (a separate
  trust domain from staff/admin logins). See `docs/architecture_mapping.md`.
- **Data-quality faults and intrusion-style attacks are the same statistical territory.**
  A slow, gradual data-injection attack looks like drift; a single fabricated reading looks
  like a spike. `dq_engine/checks/integrity.py` adds two tampering-focused checks; one shipped,
  one was found non-viable against this real dataset and documented as a limitation rather
  than hidden. See `docs/intrusion_detection.md`.
