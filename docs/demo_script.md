# Review demo script (about 12 minutes)

Before starting: database up (`docker compose up -d`), API and dashboard running (see README),
browser on http://localhost:4200. Optionally start the live feed in a third terminal. Sign in
as `staff@coldsense.lk` to start (password `ColdSense#2025` for every account).

## 1. The problem (1 min)
Cold-chain sensors are trusted blindly. Drift, dropouts, noise and calibration errors quietly
corrupt the data, and decisions made on it cost real money. ColdSense-DQ puts a data quality
layer between ingestion and analytics, and an enterprise access model in front of both.

## 2. Sign in as Staff -- the view-only role (1 min)
- Note the sidebar shows only the four viewing pages; no Reports, no User Management.
- Go to **Data Quality** and try the fault-injection buttons: they're disabled, with a notice
  explaining the required role. Staff can see the *results* of the last demo run, not trigger one.
- Go to **Alerts**: no Export CSV button, no Acknowledge column. Same data, fewer actions --
  the role boundary is about what you can *do*, not what you can *see*, for this system.

## 3. Real-Time Monitoring (2 min)
- Three modeled cold-storage units, each with a **data trust score** next to its temperature.
  The score is computed live from the newest 30 minutes, using the same code as the batch job.
- Click **Walk-in Cooler Unit 3** and set the range to **72 h**. Point out the dashed safe limit
  and the stretches above it, and the red triangles: readings the quality layer flagged and
  excluded rather than plotted into the line.
- Point out the forecast line under each card -- it's an ARIMA model (order chosen automatically
  by AIC each time), not a plain trend line; it falls back to a simple trend if there isn't
  enough recent clean data to fit.
- If the live feed is running, watch the readings and the trust badge update. Mention that
  the feed doesn't write to the database directly -- it pushes each reading to the real
  ingestion API (`POST /api/ingest/readings`), authenticated with a device key, the same
  endpoint a physical sensor's gateway would call. This is the "Sensor Data Stream" path
  in the architecture diagram, kept distinct from retail data's "Batch Upload" path.

## 4. Sign out, sign in as Quality Supervisor (1 min)
- Sidebar now includes **Reports**. Back on **Data Quality**, the fault-injection buttons are
  enabled and the role notice is gone.

## 5. Data Quality -- the core contribution (4 min)
- Per-unit summary: 405K real readings, under 0.3% excluded as row-level glitches. Most
  windows score High; Walk-in Cooler Unit 3 has a meaningful chunk at Low -- say plainly
  that's correct, not a bug: those are windows containing one of the real glitch readings
  the physically-impossible-jump check (below) is specifically designed to catch hard.
- **Fault injection demo** on Walk-in Cooler Unit 3, in this order:
  1. *Interference spike*: detected and flagged, but the window stays High because the bad
     reading is excluded downstream. Isolated faults are handled at row level.
  2. *Sensor drift*: window drops to Medium. Drift biases every reading, so the whole window
     loses trust.
  3. *Sensor dropout*: about 5 minutes of missing data, trust falls.
  4. *Calibration error (gross)*: physically impossible values, trust collapses to Low.
  5. *Calibration error (subtle)*: **Not detected.** Explain why: a constant in-range offset is
     indistinguishable from a warmer unit using one sensor's data. It needs cross-sensor
     comparison or reference calibration, which is stated future work. Naming a limit yourself
     is stronger than being asked about it.
  6. *Data injection attack*: trust collapses to Low (20), flagged `integrity`. Frame this as
     the intrusion-detection angle: a fabricated reading beyond the unit's thermal mass is
     treated as a confirmed tampering signature, the same reasoning anomaly-based intrusion
     detection applies to IoT sensor networks (see `docs/intrusion_detection.md`).
  7. *Replay attack*: **Not detected**, and say so proactively. This sensor hardware caches a
     reading between its own internal sampling cycles, so exact-repeat values cover ~83% of
     real readings naturally -- there's no threshold that separates that from an injected
     frozen value using this sensor's data alone. This was investigated and validated against
     real data, then deliberately left out of the live score rather than shipped broken. Two
     honestly-named blind spots (this and calibration bias) is a stronger position than zero.
- If asked about unauthorized access (not data tampering): scroll to **Ingestion Security**
  on this page -- logs every failed device-key attempt and every push from an unregistered
  device_id, separate from the data-quality flags above.

## 6. Alerts, Impact and Reports (2 min)
- On **Alerts**, acknowledge one (button is now enabled): it records who acknowledged it and
  when, visible to every role on that row.
- **Impact**: only the Walk-in Cooler breached its limit (chiefly days 4, 7 and 8). Revenue at
  risk = that day's sales of the products stored there x a spoilage probability that grows with
  degree-minutes above the limit.
- **Reports**: pick the unit(s) and the date range (defaults to the dataset's own 8-day window),
  preview the combined quality/alerts/impact summary, then **Download PDF** -- the same data,
  formatted for handing to someone who won't open the dashboard.
- State the caveats: the impact model is a documented heuristic, and the sensor/sales linkage
  is modeled because the real data is confidential.

## 7. Sign in as Administrator (1 min)
- **User Management**: create an account, change a role, disable one. This is the only role
  that can do this -- Operations Manager sits between Supervisor and Admin in the hierarchy but
  has no extra capability yet (see `docs/access_control.md` for why that's a deliberate,
  stated placeholder, not an oversight).

## Likely questions
- **Why do most windows score High?** The data is mostly clean; the layer is not tuned to
  manufacture problems. The false-positive fix (a resolution floor on the outlier check, which
  had flagged ~10% of readings) is a good story about validating against real data.
- **Why are units at ~22 C called freezers?** The public dataset is room-level sensors. The
  analytics layer maps each unit onto a realistic setpoint (documented, labelled in the UI).
- **Why does Operations Manager have the same permissions as Quality Supervisor?** The
  hierarchy is real and enforced (`role_level` in the database, checked server-side on every
  request), but no feature in this phase specifically needs a middle tier yet -- e.g. threshold
  configuration would be a natural fit later.
- **Is the RBAC actually enforced, or just hidden in the UI?** Server-side: every protected
  endpoint checks the JWT and role independently (open the browser devtools Network tab and
  call `/api/admin/users` as Staff to show the 403).
- **Is the "real-time" claim genuine, or is this just reading a static dataset?** Two
  separate things have to be true, and both are: (1) new readings arrive through a real
  ingestion endpoint, not a direct database write (`docs/architecture_mapping.md`), and
  (2) quality scoring and the forecast are recomputed fresh on every dashboard request
  from whatever is newest in the database, not precomputed once. Offer to run
  `curl -X POST .../api/ingest/readings` live and show the dashboard react.
- **How does this relate to intrusion detection?** A slow data-injection attack on a sensor
  looks statistically identical to natural drift; a single fabricated reading looks like a
  spike -- the same territory anomaly-based intrusion detection occupies on IoT/industrial
  networks. Two things are built on that: access-layer logging of unauthorized ingestion
  attempts (Ingestion Security panel), and a data-layer physically-impossible-jump check
  treating a fabricated reading as a confirmed tampering signature. See
  `docs/intrusion_detection.md` for the full writeup, including the replay-detection attempt
  that was investigated and found not viable against this real dataset.
- **What's next?** Cross-sensor calibration checks, alert notifications, audit logging, then
  validation on real John Keells data.
