# Architecture Diagram to Implementation

This maps every box in the proposal's "Proposed System Architecture" diagram to the code
that implements it, so the diagram can be defended box-by-box at review rather than taken
on faith. The diagram draws two distinct ingestion paths -- **"Sensor Data Stream"** for
IoT and **"Batch Upload"** for retail sales -- and that distinction is real in the code,
not just in the picture.

| Diagram box | Implementation |
|---|---|
| **IoT Sensors** (Temp & Humidity, Timestamp) -- *Sensor Data Stream* | No physical sensors exist for this project (the real industry data is confidential, per the proposal). `ingestion/simulate_stream.py` stands in for a sensor's gateway: it pushes one reading at a time, at the original sampling pace, to the same HTTP endpoint a real gateway would call. |
| **Sensor Data Stream** (the arrow itself) | `POST /api/ingest/readings` and `/ingest/readings/batch` (`backend/app/routers/ingest.py`). Authenticated with a device API key (`X-Device-Key`), not a user login -- machines and dashboard users are different trust domains. Validates the payload is *structurally* sound (known device, right types); it does not judge whether a reading is *trustworthy* -- that's the Data Quality layer's job, next. |
| **Retail Sales Data** -- *Batch Upload* | `ingestion/load_sales_data.py`, run as a one-off/periodic batch job against the supermarket sales CSV, matching the diagram's own "Batch Upload" label -- retail data isn't claimed to be real-time, only sensor data is. |
| **Data Quality Layer** (Missing Value Detection, Anomaly Checks, Data Reliability Scoring) | `dq_engine/`: `checks/missing.py`, `checks/outlier.py`, `checks/drift.py`, `checks/noise.py`, combined into a trust score by `scoring.py`. Runs twice: (1) batch, over full history (`dq_engine/run_scoring.py`), and (2) **live** -- every `GET /api/units` call recomputes it fresh on the newest ~30 minutes (`backend/app/routers/units.py`). The live path is what makes the dashboard's trust score a genuinely real-time number, not a once-a-day batch figure. |
| **Enterprise Data Lake** (Clean & Verified Data Storage) | PostgreSQL/TimescaleDB (`docker-compose.yml`, `db/init.sql`). "Verified" is realized as row-level (`reading_flag`) and window-level (`quality_score`) verdicts *tagging* the raw data, rather than physically duplicating a separate "clean" copy -- analytics excludes flagged readings at read time (`analytics/prepare.py`). Functionally equivalent to a verified partition, without copy-on-write duplication. |
| **Anomaly Detection / Trend Forecasting** | `analytics/anomaly.py` (rule-based safe-limit excursions + Isolation Forest, proposal ref [5]) and `analytics/arima.py` (ARIMA, proposal ref [6]; falls back to `analytics/forecast.py`'s linear trend if a fit doesn't converge). |
| **Impact Analysis** (Spoilage Risk & Business Impact) | `analytics/business_impact.py`, persisted by `analytics/run_analytics.py` into `impact_estimate`. See `docs/data_linkage.md` for the spoilage-probability heuristic. |
| **Insights & Alerts** (looping back into the Data Lake) | `anomaly_event`, `impact_estimate`, `reading_flag` and `quality_score` -- these tables *are* the stored insights/alerts, queried back out by the API for the dashboard and for the Reports PDF. |
| **Decision Dashboard** | `frontend/`: Real-Time Monitoring, Data Quality, Alerts, Risk & Business Impact. |

## Built beyond the one-page diagram

Login, role-based access, User Management and the Reports page aren't boxes in this
diagram, but they follow directly from the proposal's own framing of this as an
**enterprise** data platform (page 6: "Build an enterprise data platform") and from the
written report's fuller discussion of a multi-view dashboard. See `docs/access_control.md`
for the role model.

## Where this still falls short of a real deployment

- `simulate_stream.py` is a demo stand-in, not a physical sensor -- it proves the
  ingestion *path* works, not that a sensor exists.
- No device provisioning/rotation for the device API key; one shared key for all devices
  is fine for a demo, not for production (a real deployment would issue one key or
  certificate per device).
- No message broker/queue (e.g. MQTT) between device and API -- readings are pushed
  directly over HTTP, one request per reading (or per batch). A production IoT
  deployment would more likely use MQTT with a broker in front of this same ingestion
  logic; the endpoint itself wouldn't need to change.
