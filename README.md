# ColdSense-DQ

Data-quality-driven cold-chain intelligence platform for retail SMEs. IoT temperature/humidity
streams and retail sales data are validated by a data quality layer *before* any analytics
touches them, so alerts and business-impact estimates rest on data that has earned trust.

See the project proposal for the problem statement and architecture. This README covers running
what is built (mid-review phase): ingestion, storage, the data quality framework, analytics
(anomaly detection, ARIMA forecasting, business impact), a role-gated API, and a web dashboard
with authentication, reporting and user management.

## Prerequisites

- Python 3.11+
- Docker Desktop (PostgreSQL + TimescaleDB)
- Node.js 22.12+ (the dashboard uses Angular 20)
- A Kaggle API token at `~/.kaggle/kaggle.json` (to download the datasets)

## First-time setup

```bash
# 1. Database (schema in db/init.sql loads automatically on first start)
docker compose up -d

# 2. Python environment
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
cp .env.example .env

# 3. Datasets
kaggle datasets download -d garystafford/environmental-sensor-data-132k -p data/raw --unzip
kaggle datasets download -d zoroxide/supermarket-sales -p data/raw --unzip

# 4. Data pipeline: ingest -> data quality scoring -> analytics (order matters)
cd ingestion && python load_sales_data.py && python load_sensor_data.py && cd ..
python dq_engine/run_scoring.py
python analytics/run_analytics.py

# 5. Demo user accounts (admin/operations manager/quality supervisor/staff -- see docs/access_control.md)
python backend/seed_users.py

# 6. Dashboard dependencies
cd frontend && npm install && cd ..
```

Re-running steps 4's scoring and analytics scripts is safe; they clear their own output first.

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
python simulate_stream.py 1c:bf:ce:15:ec:4d          # Walk-in Cooler Unit 3, ~15 min of data
python simulate_stream.py --cleanup                  # remove the replayed rows afterwards
```

Open http://localhost:4200 and sign in with one of the seeded accounts (password
`ColdSense#2025` for all): `admin@coldsense.lk`, `ops.manager@coldsense.lk`,
`supervisor@coldsense.lk`, `staff@coldsense.lk`. What each role can do is in
`docs/access_control.md`. `docs/demo_script.md` is a suggested walkthrough for the review.

## Tests

```bash
python -m pytest dq_engine/tests -v
```

The tests inject synthetic faults (dropout, spike, drift, noise, duplicate timestamps,
calibration bias, replay/frozen signal, physically impossible jump) into a clean window and
assert the matching check fires, and stays quiet on clean data. They also pin down two
documented blind spots (subtle calibration bias, replay detection -- see
`docs/intrusion_detection.md`).

## Layout

```
db/init.sql         schema: sensor_unit, sensor_reading (hypertable), quality_score, reading_flag,
                    anomaly_event, impact_estimate, sales_transaction, app_user,
                    alert_acknowledgement, security_event
ingestion/          CSV loaders (batch) + live-feed simulator (calls the ingestion API, see below)
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
- **The datasets are stand-ins and are linked by a modeled join.** See `docs/data_linkage.md`
  for the setpoint mapping, the relative-day join, the impact heuristic and known limitations.
- **The live trust score is the real thing.** The API scores the newest 30 minutes with the same
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
